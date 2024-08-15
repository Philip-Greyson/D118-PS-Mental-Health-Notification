"""

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

# importing module
import acme_powerschool
import oracledb  # needed for connection to PowerSchool server (ordcle database)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from email.message import EmailMessage

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
FIRST_NOTIFY_THRESHOLD = 3  # when this number of the code above is reached it will send the 1st notification
SECOND_NOTIFY_THRESHOLD = 5  # when this number of the code above is reached it will send the 2nd notification


def ps_update_custom_field(table: str, field: str, dcid: int, value) -> str:
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
        print(f'ERROR: Could not update field {field}  in table {table} for student DCID {dcid}, status {result.json().get('results').get('result')}')
        print(f'ERROR: Could not update field {field}  in table {table} for student DCID {dcid}, status {result.json().get('results').get('result')}', file=log)
    else:
        print(f'DBUG: Successfully updated field {field} in table {table} for student DCID {dcid} to {value}')
        print(f'DBUG: Successfully updated field {field} in table {table} for student DCID {dcid} to {value}', file=log)
    return statusCode

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

        ps = acme_powerschool.api('d118-powerschool.info', client_id=d118_client_id, client_secret=d118_client_secret) # create ps object via the API to do requests on

        # create the connecton to the PowerSchool database
        with oracledb.connect(user=DB_UN, password=DB_PW, dsn=DB_CS) as con:
            with con.cursor() as cur:  # start an entry cursor
                print(f'INFO: Connection established to PS database on version: {con.version}')
                print(f'INFO: Connection established to PS database on version: {con.version}', file=log)

                # get the term year number which is used to search the attendance codes table for the correct code to pass to attendance
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
                if not termYear:  # if we could not find a term year that contained todays date
                    print(f'ERROR: Could not find a matching term year for todays date, ending execution')
                    print(f'ERROR: Could not find a matching term year for todays date, ending execution', file=log)
                    sys.exit()  # end the script

                # get a map of school code to attendance codes from the attendance_code table
                attendanceCodeMap = {}  # start with an empty dictionary
                cur.execute('SELECT schoolid, id FROM attendance_code WHERE yearid = :year and att_code = :code', year=termYear, code=ATTENDANCE_CODE)
                codes = cur.fetchall()
                for code in codes:
                    attendanceCodeMap.update({code[0]: code[1]})  # add the school:id map to the dictionary
                print(f'DBUG: attendance code IDs: {attendanceCodeMap}')
                print(f'DBUG: attendance code IDs: {attendanceCodeMap}', file=log)

                # start going through students one at a time
                cur.execute('SELECT stu.student_number, stu.id, stu.dcid, stu.first_name, stu.last_name, stu.schoolid, schools.abbreviation, stufields.custom_counselor_email, stufields.custom_deans_house_email, stufields.custom_social_email, stufields.custom_psych_email, absent.mentalhealth_notified, absent.mentalhealth_notified_2\
                             FROM students stu LEFT JOIN schools ON stu.schoolid = schools.school_number LEFT JOIN u_studentsuserfields stufields ON stu.dcid = stufields.studentsdcid LEFT JOIN u_chronicabsenteeism absent ON stu.dcid = absent.studentsdcid WHERE stu.enroll_status = 0')
                students = cur.fetchall()
                for student in students:
                    stuNum = int(student[0])  # normal ID number
                    stuID = int(student[1])  # ps internal ID number, used in attendance table
                    stuDCID = int(student[2])
                    firstName = str(student[3])
                    lastName = str(student[4])
                    school = int(student[5])
                    schoolAbbrev = str(student[6])
                    guidanceCounselorEmail = str(student[7])
                    deansEmail = str(student[8])
                    socialWorkerEmail = str(student[9])
                    psychologistEmail = str(student[10])
                    firstNotification = True if student[11] == 1 else False
                    secondNotification = True if student[12] == 1 else False
                    absenceCode = attendanceCodeMap.get(school)  # get the specific mental health code for the building the student is in
                    # do the query of attendance table for the mental health day code
                    cur.execute("SELECT studentid, schoolid, dcid, att_date FROM attendance WHERE ATT_MODE_CODE = 'ATT_ModeDaily' AND studentid = :student AND attendance_codeid = :code AND YEARID = :year", student=stuID, code=absenceCode, year=termYear)
                    entries = cur.fetchall()
                    if len(entries) > 0:
                        print(f'DBUG: Student {stuNum} has taken {len(entries)} mental health day(s) in year code {termYear}')
                        print(f'DBUG: Student {stuNum} has taken {len(entries)} mental health day(s) in year code {termYear}', file=log)
                        for entry in entries:
                            print(f'DBUG: {stuNum} took a mental health day on at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}')
                            print(f'DBUG: {stuNum} took a mental health day on at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}', file=log)
                        if (FIRST_NOTIFY_THRESHOLD <= len(entries) < SECOND_NOTIFY_THRESHOLD) and not firstNotification:  # if we have met the threshold for stage 1 and the notification has not already been sent, send an email
                            toEmail = schoolAbbrev + EMAIL_GROUP_SUFFIX  # make the school specific email group string
                            if school == 5:
                                toEmail = f'{toEmail},{guidanceCounselorEmail},{deansEmail},{socialWorkerEmail},{psychologistEmail}'  # if we are at the high school, need to add their specific student service team
                            print(f'INFO: {stuNum} has reached the warning threshold of {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}')
                            print(f'INFO: {stuNum} has reached the warning threshold of {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}', file=log)
                            try:
                                mime_message = EmailMessage()  # create an email message object
                                # define headers
                                mime_message['To'] = toEmail
                                mime_message['Subject'] = f'{len(entries)} Mental Health Days Taken For {stuNum} - {firstName} {lastName}'  # subject line of the email
                                mime_message.set_content(f'This email is to warn you that {stuNum} - {firstName} {lastName} has reached {len(entries)} mental health excused absences for this school year. Please take the appropriate steps to address this with the student and parent/guardian.')  # body of the email
                                # encoded message
                                encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                create_message = {'raw': encoded_message}
                                send_message = (service.users().messages().send(userId="me", body=create_message).execute())
                                print(f'DBUG: Email sent, message ID: {send_message["id"]}') # print out resulting message Id
                                print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                # update the notificaton field to be true so that we dont sent more than one email a year
                                ps_update_custom_field('u_chronicabsenteeism', 'mentalhealth_notified', stuDCID, True)

                            except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                status = er.status_code
                                details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                print(f'ERROR {status} from Google API while sending mental health notification email: {details["message"]}. Reason: {details["reason"]}')
                                print(f'ERROR {status} from Google API while sending mental health notification email: {details["message"]}. Reason: {details["reason"]}', file=log)
                            except Exception as er:
                                print(f'ERROR while sending mental health notification for student {stuNum}: {er}')
                                print(f'ERROR while sending mental health notification for student {stuNum}: {er}', file=log)

                        elif (len(entries) >= SECOND_NOTIFY_THRESHOLD) and not secondNotification:  # if we have met the threshold for stage 2 and the notification has not already been sent, send an email
                            toEmail = schoolAbbrev + EMAIL_GROUP_SUFFIX  # make the school specific email group string
                            if school == 5:
                                toEmail = f'{toEmail},{guidanceCounselorEmail},{deansEmail},{socialWorkerEmail},{psychologistEmail}'  # if we are at the high school, need to add their specific student service team
                            print(f'INFO: {stuNum} has reached the max threshold with {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}')
                            print(f'INFO: {stuNum} has reached the max threshold with {len(entries)} mental health days and a notification has not been sent, sending email to {toEmail}', file=log)
                            try:
                                mime_message = EmailMessage()  # create an email message object
                                # define headers
                                mime_message['To'] = toEmail
                                mime_message['Subject'] = f'Maximum Mental Health Days Taken For {stuNum} - {firstName} {lastName}'  # subject line of the email
                                mime_message.set_content(f'This email is to inform you that {stuNum} - {firstName} {lastName} has reached the maximum allowed mental health excused absences of {len(entries)} for this school year. Please take the appropriate steps to address this with the student and parent/guardian.')  # body of the email
                                # encoded message
                                encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                create_message = {'raw': encoded_message}
                                send_message = (service.users().messages().send(userId="me", body=create_message).execute())
                                print(f'DBUG: Email sent, message ID: {send_message["id"]}') # print out resulting message Id
                                print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                # update the notificaton field to be true so that we dont sent more than one email a year
                                ps_update_custom_field('u_chronicabsenteeism', 'mentalhealth_notified_2', stuDCID, True)

                            except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                status = er.status_code
                                details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                print(f'ERROR {status} from Google API while sending mental health notification email: {details["message"]}. Reason: {details["reason"]}')
                                print(f'ERROR {status} from Google API while sending mental health notification email: {details["message"]}. Reason: {details["reason"]}', file=log)
                            except Exception as er:
                                print(f'ERROR while sending mental health notification for student {stuNum}: {er}')
                                print(f'ERROR while sending mental health notification for student {stuNum}: {er}', file=log)

        endTime = datetime.now()
        endTime = endTime.strftime('%H:%M:%S')
        print(f'INFO: Execution started at {endTime}')
        print(f'INFO: Execution started at {endTime}', file=log)