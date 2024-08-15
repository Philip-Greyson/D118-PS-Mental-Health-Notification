# # D118-PS-Mental-Health-Notification

This is an pretty specific script D118 uses to send an email after a certain number of specific attendance codes have been reached for students.

## Overview

The purpose of this script is to send an email to a group when a student reaches two thresholds of mental health days taken, one at 3 days and one at 5 days. This is done by finding the current termYear via the terms table, then searching for matching attendance ids that correlate to each building for the code of "MH" (our notation for mental health days). Then each active student is processed, and all mental health days retrieved for each student. If their number of mental health days exceeds the first threshold, an email is sent to an email group constructed from the school abbreviation (so that each building has different recipients), and then a custom field is updated using the ACME PowerSchool API plugin, so that the notification is not sent again in the future. Similarly, if the number of days meets or exceeds the second threshold, a second email is sent and a second custom field updated.

## Requirements

The following Environment Variables must be set on the machine running the script:

- POWERSCHOOL_READ_USER
- POWERSCHOOL_DB_PASSWORD
- POWERSCHOOL_PROD_DB
- POWERSCHOOL_API_ID
- POWERSCHOOL_API_SECRET

These are fairly self explanatory, and just relate to the usernames, passwords, and host IP/URLs for PowerSchool, as well as the API ID and secret you can get from creating a plugin in PowerSchool. If you wish to directly edit the script and include these credentials or to use other environment variable names, you can.

Additionally, the following Python libraries must be installed on the host machine (links to the installation guide):

- [Python-oracledb](https://python-oracledb.readthedocs.io/en/latest/user_guide/installation.html)
- [Python-Google-API](https://github.com/googleapis/google-api-python-client#installation)
- [ACME PowerSchool](https://easyregpro.com/acme/pythonAPI/README.html)

In addition, an OAuth credentials.json file must be in the same directory as the overall script. This is the credentials file you can download from the Google Cloud Developer Console under APIs & Services > Credentials > OAuth 2.0 Client IDs. Download the file and rename it to credentials.json. When the program runs for the first time, it will open a web browser and prompt you to sign into a Google account that has the permissions to send emails. Based on this login it will generate a token.json file that is used for authorization. When the token expires it should auto-renew unless you end the authorization on the account or delete the credentials from the Google Cloud Developer Console. One credentials.json file can be shared across multiple similar scripts if desired.
There are full tutorials on getting these credentials from scratch available online. But as a quickstart, you will need to create a new project in the Google Cloud Developer Console, and follow [these](https://developers.google.com/workspace/guides/create-credentials#desktop-app) instructions to get the OAuth credentials, and then enable APIs in the project (the Admin SDK API is used in this project).

## Customization

This script is very specific to our district's use case, so it is going to be a lot of work to customize for your needs.

- To begin with, you will need to change the SQL query to match the custom tables/fields for the notification fields. We also include custom fields with the social services team emails so the highschoolers can have the emails sent to their specific team, so those fields either need to be updated to ones you use or the functionality removed by deleting the `if school ==5` statement.
  - If your custom fields for the notifications are going to be different, you will also need to update the calls to `ps_update_custom_field` with the appropriate table and field name
- This script finds the term year by searching one building for all valid year terms, I use school=5 for this but if you do not have a school with building ID of 5 you will need to change that.
- You can change `ATTENDANCE_CODE` to change the specific attendance code to search for
- `FIRST_NOTIFY_THRESHOLD` is the number at which the first email will be sent and the first notification field updated
- `SECOND_NOTIFY_THRESHOLD` is the number at which the second and final email will be sent and the second notification field updated
