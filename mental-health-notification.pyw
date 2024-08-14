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
NOTIFY_THRESHOLD = 5  # when this number of the code above is reached it will send the notification

def ps_update_custom_field(table: str, field: str, dcid: int, value) -> str:
    """Function to do the update of a custom field in a student extension table, so that the large json does not need to be used every time an update is needed elsewhere."""
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
        print(f'ERROR: Could not update field {field}  in table {table} for student DCIC {dcid}, status {result.json().get('results').get('result')}')
        print(f'ERROR: Could not update field {field}  in table {table} for student DCIC {dcid}, status {result.json().get('results').get('result')}', file=log)
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
                print(f'DBUG attendance code IDs: {attendanceCodeMap}')

                # start going through students one at a time
                cur.execute('SELECT stu.student_number, stu.id, stu.dcid, stu.first_name, stu.last_name, stu.schoolid, schools.abbreviation, stufields.custom_counselor_email, stufields.custom_deans_house_email, stufields.custom_social_email, stufields.custom_psych_email\
                             FROM students stu LEFT JOIN schools ON stu.schoolid = schools.school_number LEFT JOIN u_studentsuserfields stufields ON stu.dcid = stufields.studentsdcid WHERE stu.enroll_status = 0')
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
                    # do the query of attendance table for the mental health day code
                    cur.execute('SELECT * FROM attendance WHERE studentid = 51274')
                    # entries = cur.fetchall()
                    # for entry in entries:
                    #     print(entry)