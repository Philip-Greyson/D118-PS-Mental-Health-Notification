"""Script to send out notifications when students reach certain amounts of specific attendance codes for the year.

https://github.com/Philip-Greyson/D118-PS-Mental-Health-Notification

Needs the google-api-python-client, google-auth-httplib2 and the google-auth-oauthlib:
pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
also needs oracledb: pip install oracledb --upgrade
finally needs the ACME powerschool library downloaded from https://easyregpro.com/acme.php
"""

import base64
import json
import os  # needed for environement variable reading
import sys
from datetime import *
from email.message import EmailMessage

# importing module
import acme_powerschool
import oracledb  # needed for connection to PowerSchool server (ordcle database)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# setup db connection
DB_UN = os.environ.get('POWERSCHOOL_READ_USER')  # username for read-only database user
DB_PW = os.environ.get('POWERSCHOOL_DB_PASSWORD')  # the password for the database account
DB_CS = os.environ.get('POWERSCHOOL_PROD_DB')  # the IP address, port, and database name to connect to
print(f'DBUG: Database Username: {DB_UN} |Password: {DB_PW} |Server: {DB_CS}')  # debug so we can see where oracle is trying to connect to/with

d118_client_id = os.environ.get("POWERSCHOOL_API_ID")
d118_client_secret = os.environ.get("POWERSCHOOL_API_SECRET")

# Google API Scopes that will be used. If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.compose']

EMAIL_GROUP_SUFFIX = '-mental-notifications@d118.org'  # a suffix to be appended to the school abbreviations and will make up the group email
ATTENDANCE_CODE = 'MH'  # the attendance code we will actually search for
FIRST_NOTIFY_THRESHOLD = 3  # when this number of the code above is reached for the first time it will send the 1st notification
SECOND_NOTIFY_THRESHOLD = 5  # when this number of the code above is reached for the first time it will send the 2nd notification. When it is greater than this number it will send a warning every time
DO_PARENT_NOTIFICATIONS = True
PARENT_NOTIFY_SCHOOLIDS = [5]  # list of school codes that will get the parent notification portion
TEST_RUN = False  # flag for shifting to testing mode where all emails are sent to specific test email, and custom fields are not updated
TEST_EMAIL = ''  # the email that will be used for testing mode

def ps_update_custom_field(table: str, field: str, dcid: int, value: any) -> str:
    """Function to do the update of a custom field in a student extension table, so that the large json does not need to be used every time an update is needed elsewhere."""
    # print(f'DBUG: table {table}, field {field}, student DCID {dcid}, value {value}')
    try:
        data = {
            'students' : {
                'student': [{
                    '@extensions': table,
                    'id' : str(dcid),
                    'client_uid' : str(dcid),
                    'action' : 'UPDATE',
                    '_extension_data': {
                        '_table_extension': [{
                            'name': table,
                            '_field': [{
                                'name': field,
                                'value': value
                            }]
                        }]
                    }
                }]
            }
        }
        result = ps.post(f'ws/v1/student?extensions={table}', data=json.dumps(data))
        statusCode = result.json().get('results').get('result').get('status')
    except Exception as er:
        print(f'ERROR while trying to update custom field {field} in table {table} for student DCID {dcid}: {er}')
        print(f'ERROR while trying to update custom field {field} in table {table} for student DCID {dcid}: {er}')
        return 'ERROR'
    if statusCode != 'SUCCESS':
        print(f"ERROR: Could not update field {field}  in table {table} for student DCID {dcid}, status {result.json().get('results').get('result')}")
        print(f"ERROR: Could not update field {field}  in table {table} for student DCID {dcid}, status {result.json().get('results').get('result')}", file=log)
    else:
        print(f'DBUG: Successfully updated field {field} in table {table} for student DCID {dcid} to {value}')
        print(f'DBUG: Successfully updated field {field} in table {table} for student DCID {dcid} to {value}', file=log)
    return statusCode

def get_custody_contacts(student_dcid:int) -> list:
    """Function to take a student DCID number and return dictionary of the contacts with custody names and emails."""
    try:
        cur.execute('SELECT p.firstname, p.lastname, email.emailaddress FROM studentcontactassoc sca \
                    LEFT JOIN studentcontactdetail scd ON scd.studentcontactassocid = sca.studentcontactassocid \
                    LEFT JOIN personemailaddressassoc pemail ON pemail.personid = sca.personid \
                    LEFT JOIN emailaddress email ON email.emailaddressid = pemail.emailaddressid \
                    LEFT JOIN person p ON sca.personid = p.id \
                    WHERE sca.studentdcid = :dcid AND scd.isactive = 1 AND scd.iscustodial = 1 AND pemail.isprimaryemailaddress = 1', dcid=student_dcid)
        custodians = cur.fetchall()
        print(f'DBUG: Number of contacts with custody and current emails for DCID {student_dcid}: {len(custodians)} - {custodians}')
        print(f'DBUG: Number of contacts with custody and current emails for DCID {student_dcid}: {len(custodians)} - {custodians}', file=log)
    except Exception as er:
        print(f'ERROR while getting contacts with custodial access for student DCID {student_dcid}: {er}')
        print(f'ERROR while getting contacts with custodial access for student DCID {student_dcid}: {er}', file=log)
    return custodians if len(custodians) > 0 else None  # if we had results, return the list of tuples, otherwise just return None

def email_custodial_contacts(student_dcid:int, student_number:int, language:str, threshold:int, days:int) -> None:
    """Function to send the emails to parents/guardians about the number of mental health days."""
    if threshold == 1:
        contactsToEmail = get_custody_contacts(student_dcid)
        if contactsToEmail:
            for contact in contactsToEmail:
                try:
                    contactFirstLast = f'{contact[0]} {contact[1]}'  # get their name in one string
                    toEmail = str(contact[2])
                    print(f'INFO: Student {student_number} has reached the first threshold with {days} mental health days, sending email to contact {contactFirstLast} at {toEmail}, requested language is {language}')
                    print(f'INFO: Student {student_number} has reached the first threshold with {days} mental health days, sending email to contact {contactFirstLast} at {toEmail}, requested language is {language}', file=log)
                    mime_message = EmailMessage()  # create an email message object
                    # define headers
                    if TEST_RUN:
                        mime_message['To'] = TEST_EMAIL
                    else:
                        mime_message['To'] = toEmail
                    if requestedLanguage == 'Spanish':
                        mime_message['Subject']  = 'Uso de Días de Salud Mental del Estudiante'  # subject line of the email
                        mime_message.set_content(f'Estimado/a {contactFirstLast}:\nEsperamos que este mensaje le encuentre bien. Nos comunicamos con usted para informarle que su estudiante ha utilizado {days} de los días de salud mental que tiene asignados durante el presente año escolar.\n\nReconocemos la importancia de la salud mental y el bienestar en el éxito y desarrollo integral de nuestros estudiantes. Según las pautas de la Junta de Educación del Estado de Illinois (ISBE, por sus siglas en inglés), los estudiantes tienen derecho a hasta 5 días de salud mental por cada año escolar. Estos días están destinados a apoyar a los estudiantes en el manejo de su bienestar emocional y psicológico.\n\nLe animamos a seguir monitoreando y apoyando la salud de su estudiante, y le invitamos a comunicarse con nuestros consejeros escolares o el personal de Servicios Estudiantiles si considera que se necesita apoyo adicional. Nuestro equipo está aquí para ayudar tanto a los estudiantes como a las familias a enfrentar estos retos.\n\nSi tiene alguna pregunta o necesita más asistencia, no dude en ponerse en contacto con nosotros.\n\nGracias por colaborar con nosotros para apoyar el bienestar de su estudiante.')
                    else:
                        mime_message['Subject']  = 'Student Mental Health Days Usage'  # subject line of the email
                        mime_message.set_content(f'Dear {contactFirstLast},\nWe hope this message finds you well. We are reaching out to inform you that your student has utilized {days} of their allotted mental health days during the current school year.\n\nWe recognize the importance of mental health and wellbeing in our students\' success and overall development. As per the Illinois State Board of Education (ISBE) guidelines, students are permitted up to 5 mental health days each school year. These days are intended to support students in managing their emotional and psychological wellbeing.\n\nWe encourage you to continue monitoring and supporting your student\'s health, and please feel free to reach out to our school counselors or Student Services staff if you believe additional support is needed. Our team is here to assist both students and families in navigating these challenges.\n\nShould you have any questions or need further assistance, do not hesitate to contact us.\n\nThank you for partnering with us to support your student\'s wellbeing.')
                    encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                    create_message = {'raw': encoded_message}
                    send_message = (service.users().messages().send(userId="me", body=create_message).execute())  # send the email
                    print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                    print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                    if not TEST_RUN:
                        ps_update_custom_field('u_chronicabsenteeism', 'auto_mh_notified_1', stuDCID, True)  # update the custom field so we dont sent repeat notifications
                except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                    status = er.status_code
                    details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                    print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 1st threshold with {days} absences:: {details["message"]}. Reason: {details["reason"]}')
                    print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 1st threshold with {days} absences:: {details["message"]}. Reason: {details["reason"]}', file=log)
                except Exception as er:
                    print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 1st threshold with {days} absences: {er}')
                    print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 1st threshold with {days} absences: {er}', file=log)
        else:
            print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent')
            print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent', file=log)
    elif threshold == 2:
        contactsToEmail = get_custody_contacts(student_dcid)
        if contactsToEmail:
            for contact in contactsToEmail:
                try:
                    contactFirstLast = f'{contact[0]} {contact[1]}'  # get their name in one string
                    toEmail = str(contact[2])
                    print(f'INFO: Student {student_number} has reached the second threshold with {days} mental health days, sending email to contact {contactFirstLast} at {toEmail}, requested language is {language}')
                    print(f'INFO: Student {student_number} has reached the second threshold with {days} mental health days, sending email to contact {contactFirstLast} at {toEmail}, requested language is {language}', file=log)
                    mime_message = EmailMessage()  # create an email message object
                    # define headers
                    if TEST_RUN:
                        mime_message['To'] = TEST_EMAIL
                    else:
                        mime_message['To'] = toEmail
                    if requestedLanguage == 'Spanish':
                        mime_message['Subject']  = 'Uso de Días de Salud Mental del Estudiante'  # subject line of the email
                        mime_message.set_content(f'Estimado/a {contactFirstLast}:\nNos comunicamos con usted para informarle que su estudiante ha utilizado los 5 días de salud mental asignados para el presente año escolar, según lo establecido por las pautas de la Junta de Educación del Estado de Illinois (ISBE, por sus siglas en inglés).\n\nQueremos enfatizar que la salud mental y el bienestar de su estudiante siguen siendo una de nuestras máximas prioridades. Aunque ya no quedan días de salud mental disponibles, queremos que sepa que nuestros consejeros escolares y el personal de Servicios Estudiantiles están aquí para brindar apoyo y recursos continuos a fin de ayudar a su estudiante.\n\nTenga en cuenta que cualquier ausencia adicional reportada como día de salud mental ya no se registrará como tal, sino que se clasificará como una ausencia justificada. Estas ausencias se incluirán en el registro general de asistencia de su estudiante y podrían afectar su elegibilidad para participar en actividades extracurriculares y deportes.\n\nSi considera que se necesita apoyo adicional, le animamos a ponerse en contacto con nuestro equipo de Servicios Estudiantiles para que podamos trabajar juntos y asegurar que su estudiante siga recibiendo el apoyo que necesita.\n\nGracias por colaborar con nosotros para apoyar la salud y el éxito de su estudiante.')
                    else:
                        mime_message['Subject']  = 'Important Update: Student Mental Health Days Usage'  # subject line of the email
                        mime_message.set_content(f'Dear {contactFirstLast},\nWe are reaching out to inform you that your student has now used all 5 mental health days allotted for the current school year, as outlined by Illinois State Board of Education (ISBE) guidelines.\n\nWe want to emphasize that your student\'s mental health and wellbeing remain a top priority for us. While there are no remaining mental health days available, please know that our school counselors and Student Services staff are here to provide ongoing support and resources to assist your student.\n\nPlease be advised that any further absences reported as mental health days will no longer be recorded as such and will instead be classified as excused absences. These absences will be included in your student\'s overall attendance record and may impact their eligibility to participate in extracurricular activities and athletics.\n\nIf you feel that additional support is needed, we encourage you to contact our Student Services team so we can work together to ensure your student continues to receive the support they need.\n\nThank you for your partnership in supporting your student\'s health and success.')
                    encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                    create_message = {'raw': encoded_message}
                    send_message = (service.users().messages().send(userId="me", body=create_message).execute())  # send the email
                    print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                    print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                    if not TEST_RUN:
                        ps_update_custom_field('u_chronicabsenteeism', 'auto_mh_notified_2', stuDCID, True)  # update the custom field so we dont sent repeat notifications
                except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                    status = er.status_code
                    details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                    print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 2nd threshold with {days} absences:: {details["message"]}. Reason: {details["reason"]}')
                    print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 2nd threshold with {days} absences:: {details["message"]}. Reason: {details["reason"]}', file=log)
                except Exception as er:
                    print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 2nd threshold with {days} absences: {er}')
                    print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 2nd threshold with {days} absences: {er}', file=log)
        else:
            print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent')
            print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent', file=log)

if __name__ == '__main__':
    with open('mh_notification_log.txt', 'w') as log:
        startTime = datetime.now()
        startTime = startTime.strftime('%H:%M:%S')
        print(f'INFO: Execution started at {startTime}')
        print(f'INFO: Execution started at {startTime}', file=log)
        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

        service = build('gmail', 'v1', credentials=creds)  # create the Google API service with just gmail functionality

        ps = acme_powerschool.api('d118-powerschool.info', client_id=d118_client_id, client_secret=d118_client_secret)  # create ps object via the API to do requests on

        # create the connecton to the PowerSchool database
        with oracledb.connect(user=DB_UN, password=DB_PW, dsn=DB_CS) as con:
            with con.cursor() as cur:  # start an entry cursor
                print(f'INFO: Connection established to PS database on version: {con.version}')
                print(f'INFO: Connection established to PS database on version: {con.version}', file=log)
                # get the term year number which is used to search the attendance codes table for the correct code to pass to attendance
                try:
                    today = datetime.now()  # get todays date and store it for finding the correct term later
                    termYear = None
                    cur.execute("SELECT firstday, lastday, yearid FROM terms WHERE schoolid = 5 AND isyearrec = 1 ORDER BY dcid DESC")  # get a list of terms for a random school, since every yearid should be the fine
                    terms = cur.fetchall()
                    for term in terms:  # go through every term
                        termStart = term[0]
                        termEnd = term[1]
                        #compare todays date to the start and end dates
                        if ((termStart < today) and (termEnd > today)):
                            termYear = str(term[2])
                            print(f'DBUG: Found current year ID of {termYear}')
                except Exception as er:
                    print(f'ERROR while trying to find termyear for todays date of {today}: {er}')
                    print(f'ERROR while trying to find termyear for todays date of {today}: {er}', file=log)
                if not termYear:  # if we could not find a term year that contained todays date
                    print('WARN: Could not find a matching term year for todays date to get attendance from, ending mental health notification execution')
                    print('WARN: Could not find a matching term year for todays date to get attendance from, ending mental health notification execution', file=log)
                    sys.exit()  # end the script

                # get a map of school code to attendance codes from the attendance_code table
                try:
                    attendanceCodeMap = {}  # start with an empty dictionary
                    cur.execute('SELECT schoolid, id FROM attendance_code WHERE yearid = :year and att_code = :code', year=termYear, code=ATTENDANCE_CODE)
                    codes = cur.fetchall()
                    for code in codes:
                        attendanceCodeMap.update({code[0]: code[1]})  # add the school:id map to the dictionary
                    print(f'DBUG: attendance code IDs: {attendanceCodeMap}')
                    print(f'DBUG: attendance code IDs: {attendanceCodeMap}', file=log)
                except Exception as er:
                    print(f'ERROR: Could not generated code map for year {termYear}, ending execution: {er}')
                    print(f'ERROR: Could not generated code map for year {termYear}, ending execution: {er}', file=log)
                    sys.exit()  # end the script

                # start going through students one at a time
                cur.execute('SELECT stu.student_number, stu.id, stu.dcid, stu.first_name, stu.last_name, stu.schoolid, schools.abbreviation, stufields.custom_counselor_email, stufields.custom_deans_house_email, stufields.custom_social_email, stufields.custom_psych_email, absent.mentalhealth_notified, absent.mentalhealth_notified_2, absent.auto_mh_notified_1, absent.auto_mh_notified_2, ext.hls_requestedlang \
                            FROM students stu LEFT JOIN schools ON stu.schoolid = schools.school_number \
                            LEFT JOIN u_studentsuserfields stufields ON stu.dcid = stufields.studentsdcid \
                            LEFT JOIN u_def_ext_students0 ext ON stu.dcid = ext.studentsdcid \
                            LEFT JOIN u_chronicabsenteeism absent ON stu.dcid = absent.studentsdcid \
                            WHERE stu.enroll_status = 0')
                students = cur.fetchall()
                for student in students:
                    try:
                        stuNum = int(student[0])  # normal ID number
                        stuID = int(student[1])  # ps internal ID number, used in attendance table
                        stuDCID = int(student[2])
                        firstName = str(student[3]).title()  # have it be normal capitalization, not all caps like in PS
                        lastName = str(student[4]).title()  # have it be normal capitalization, not all caps like in PS
                        school = int(student[5])
                        schoolAbbrev = str(student[6])
                        guidanceCounselorEmail = str(student[7])
                        deansEmail = str(student[8])
                        socialWorkerEmail = str(student[9])
                        psychologistEmail = str(student[10])
                        firstNotification = True if student[11] == 1 else False
                        secondNotification = True if student[12] == 1 else False
                        firstParentNotification = True if student[13] == 1 else False
                        secondParentNotification = True if student[14] == 1 else False
                        requestedLanguage = str(student[15])
                        absenceCode = attendanceCodeMap.get(school)  # get the specific mental health code for the building the student is in
                        # do the query of attendance table for the mental health day code
                        cur.execute("SELECT studentid, schoolid, dcid, att_date FROM attendance WHERE ATT_MODE_CODE = 'ATT_ModeDaily' AND studentid = :student AND attendance_codeid = :code AND YEARID = :year", student=stuID, code=absenceCode, year=termYear)
                        entries = cur.fetchall()
                        if len(entries) > 0:
                            try:
                                print(f'DBUG: Student {stuNum} has taken {len(entries)} mental health day(s) in year code {termYear}')
                                print(f'DBUG: Student {stuNum} has taken {len(entries)} mental health day(s) in year code {termYear}', file=log)
                                for entry in entries:
                                    print(f'DBUG: {stuNum} took a mental health day on at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}')
                                    print(f'DBUG: {stuNum} took a mental health day on at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}', file=log)
                                if (FIRST_NOTIFY_THRESHOLD <= len(entries) < SECOND_NOTIFY_THRESHOLD):  # if we have met the threshold for stage 1
                                    if school in PARENT_NOTIFY_SCHOOLIDS and not firstParentNotification and DO_PARENT_NOTIFICATIONS:  # if we are in a building where we need to notify parents and it hasnt been sent yet but we want to
                                        email_custodial_contacts(stuDCID, stuNum, requestedLanguage, 1, len(entries))  # call the function that will email parents/contacts with custodial rights
                                    if not firstNotification:  # if we have not already sent a notification to the school staff group
                                        toEmail = schoolAbbrev + EMAIL_GROUP_SUFFIX  # make the school specific email group string
                                        if school == 5:
                                            toEmail = f'{toEmail},{guidanceCounselorEmail},{deansEmail},{socialWorkerEmail},{psychologistEmail}'  # if we are at the high school, need to add their specific student service team
                                        print(f'INFO: {stuNum} has reached the warning threshold of {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}')
                                        print(f'INFO: {stuNum} has reached the warning threshold of {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}', file=log)
                                        try:
                                            mime_message = EmailMessage()  # create an email message object
                                            # define headers
                                            if TEST_RUN:
                                                mime_message['To'] = TEST_EMAIL
                                            else:
                                                mime_message['To'] = toEmail
                                            mime_message['Subject'] = f'{len(entries)} Mental Health Days Taken For {stuNum} - {firstName} {lastName}'  # subject line of the email
                                            mime_message.set_content(f'This email is to warn you that {stuNum} - {firstName} {lastName} has reached {len(entries)} mental health excused absences for this school year. Please take the appropriate steps to address this with the student and parent/guardian.')  # body of the email
                                            # encoded message
                                            encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                            create_message = {'raw': encoded_message}
                                            send_message = (service.users().messages().send(userId="me", body=create_message).execute())
                                            print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                                            print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                            # # update the notificaton field to be true so that we dont sent more than one email a year
                                            if not TEST_RUN:
                                                ps_update_custom_field('u_chronicabsenteeism', 'mentalhealth_notified', stuDCID, True)

                                        except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                            status = er.status_code
                                            details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                            print(f'ERROR {status} from Google API while sending mental health notification email for student {stuNum}: {details["message"]}. Reason: {details["reason"]}')
                                            print(f'ERROR {status} from Google API while sending mental health notification email for student {stuNum}: {details["message"]}. Reason: {details["reason"]}', file=log)
                                        except Exception as er:
                                            print(f'ERROR while sending mental health notification for student {stuNum}: {er}')
                                            print(f'ERROR while sending mental health notification for student {stuNum}: {er}', file=log)

                                elif (len(entries) == SECOND_NOTIFY_THRESHOLD):  # if we have met the threshold for stage 2
                                    if school in PARENT_NOTIFY_SCHOOLIDS and not secondParentNotification and DO_PARENT_NOTIFICATIONS:  # if we are in a building where we need to notify parents and it hasnt been sent yet but we want to
                                        email_custodial_contacts(stuDCID, stuNum, requestedLanguage, 2, len(entries))  # call the function that will email parents
                                    if not secondNotification:  # if we have not sent the notification to the school staff group
                                        toEmail = schoolAbbrev + EMAIL_GROUP_SUFFIX  # make the school specific email group string
                                        if school == 5:
                                            toEmail = f'{toEmail},{guidanceCounselorEmail},{deansEmail},{socialWorkerEmail},{psychologistEmail}'  # if we are at the high school, need to add their specific student service team
                                        print(f'INFO: {stuNum} has reached the max threshold with {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}')
                                        print(f'INFO: {stuNum} has reached the max threshold with {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}', file=log)
                                        try:
                                            mime_message = EmailMessage()  # create an email message object
                                            # define headers
                                            if TEST_RUN:
                                                mime_message['To'] = TEST_EMAIL
                                            else:
                                                mime_message['To'] = toEmail
                                            mime_message['Subject'] = f'Maximum Mental Health Days Taken For {stuNum} - {firstName} {lastName}'  # subject line of the email
                                            mime_message.set_content(f'This email is to inform you that {stuNum} - {firstName} {lastName} has reached the maximum allowed mental health excused absences of {len(entries)} for this school year. Please take the appropriate steps to address this with the student and parent/guardian.')  # body of the email
                                            # encoded message
                                            encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                            create_message = {'raw': encoded_message}
                                            send_message = (service.users().messages().send(userId="me", body=create_message).execute())
                                            print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                                            print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                            # update the notificaton field to be true so that we dont sent more than one email a year
                                            if not TEST_RUN:
                                                ps_update_custom_field('u_chronicabsenteeism', 'mentalhealth_notified_2', stuDCID, True)

                                        except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                            status = er.status_code
                                            details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                            print(f'ERROR {status} from Google API while sending mental health notification email for student {stuNum}: {details["message"]}. Reason: {details["reason"]}')
                                            print(f'ERROR {status} from Google API while sending mental health notification email for student {stuNum}: {details["message"]}. Reason: {details["reason"]}', file=log)
                                        except Exception as er:
                                            print(f'ERROR while sending mental health notification for student {stuNum}: {er}')
                                            print(f'ERROR while sending mental health notification for student {stuNum}: {er}', file=log)

                                # when the student has more than 5 absences (which they should not have, send a warning email)
                                elif (len(entries) > SECOND_NOTIFY_THRESHOLD):  # if we have are above the 2nd/final threshold, send an email every time until they get the days back under the threshold
                                    toEmail = schoolAbbrev + EMAIL_GROUP_SUFFIX  # make the school specific email group string
                                    if school == 5:
                                        toEmail = f'{toEmail},{guidanceCounselorEmail},{deansEmail},{socialWorkerEmail},{psychologistEmail}'  # if we are at the high school, need to add their specific student service team
                                    print(f'INFO: {stuNum} has exceeded the maximum allowed number of mental health days, currently with {len(entries)} days taken, sending email every time to {toEmail}')
                                    print(f'INFO: {stuNum} has exceeded the maximum allowed number of mental health days, currently with {len(entries)} days taken, sending email every time to {toEmail}', file=log)
                                    try:
                                        mime_message = EmailMessage()  # create an email message object
                                        # define headers
                                        if TEST_RUN:
                                            mime_message['To'] = TEST_EMAIL
                                        else:
                                            mime_message['To'] = toEmail
                                        mime_message['Subject'] = f'MAXIMUM MENTAL HEALTH DAYS EXCEEDED For {stuNum} - {firstName} {lastName}'  # subject line of the email
                                        mime_message.set_content(f'This email is to warn you that {stuNum} - {firstName} {lastName} has exceeded the maximum allowed mental health excused absences of {SECOND_NOTIFY_THRESHOLD} by currently having {len(entries)} for this school year. Please work with the student and their parent/guardian and update the attendance codes to bring their mental health absences back down to {SECOND_NOTIFY_THRESHOLD} days.')  # body of the email
                                        # encoded message
                                        encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                        create_message = {'raw': encoded_message}
                                        send_message = (service.users().messages().send(userId="me", body=create_message).execute())
                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)

                                    except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                        status = er.status_code
                                        details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                        print(f'ERROR {status} from Google API while sending mental health notification email for student {stuNum}: {details["message"]}. Reason: {details["reason"]}')
                                        print(f'ERROR {status} from Google API while sending mental health notification email for student {stuNum}: {details["message"]}. Reason: {details["reason"]}', file=log)
                                    except Exception as er:
                                        print(f'ERROR while sending mental health notification for student {stuNum}: {er}')
                                        print(f'ERROR while sending mental health notification for student {stuNum}: {er}', file=log)
                            except Exception as er:
                                print(f'ERROR while doing day counting and intial mental health notification decision for {stuNum}: {er}')
                                print(f'ERROR while doing day counting and intial mental health notification decision for {stuNum}: {er}', file=log)
                    except Exception as er:
                        print(f'ERROR while doing intial information processing or mental health attendance query for student {student[0]}: {er}')
                        print(f'ERROR while doing intial information processing or mental health attendance query for student {student[0]}: {er}', file=log)

        endTime = datetime.now()
        endTime = endTime.strftime('%H:%M:%S')
        print(f'INFO: Execution ended at {endTime}')
        print(f'INFO: Execution ended at {endTime}', file=log)
