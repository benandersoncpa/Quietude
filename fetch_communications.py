import os.path
import gspread
import streamlit as st
import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# --- CONFIGURATION ---
COMMUNICATIONS_SHEET_NAME = 'Communications'
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
COMPLETE_LABEL_ID = st.secrets["COMPLETE_LABEL_ID"]
LABEL_ID_AEGIS_EMAIL = st.secrets["LABEL_ID_AEGIS_EMAIL"]
LABEL_ID_PERSONAL_EMAIL = st.secrets["LABEL_ID_PERSONAL_EMAIL"]
LABEL_ID_AEGIS_GV = st.secrets["LABEL_ID_AEGIS_GV"]
LABEL_ID_1099_GV = st.secrets["LABEL_ID_1099_GV"]
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar.readonly'
]

def run_fetch_communications(gmail_service, gspread_client, cli_mode=False):
    """
    Fetches recent emails, logs them to a sheet, and purges old sheet entries.
    Handles Gmail API pagination to ensure all messages are retrieved.
    """
    def notify(message):
        if cli_mode:
            print(message)
        else:
            st.toast(message)

    try:
        spreadsheet = gspread_client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.worksheet(COMMUNICATIONS_SHEET_NAME)
        
        # Get existing message IDs from the sheet to prevent duplicates
        existing_ids = set(worksheet.col_values(1))

        # --- FIX: Implement pagination to fetch ALL messages ---
        all_messages = []
        page_token = None
        while True:
            results = gmail_service.users().messages().list(
                userId='me',
                q='in:inbox newer_than:7d',
                maxResults=500, # Fetch up to 500 at a time
                pageToken=page_token
            ).execute()
            
            messages = results.get('messages', [])
            all_messages.extend(messages)
            
            page_token = results.get('nextPageToken')
            if not page_token:
                break # Exit loop if there are no more pages

        if not all_messages:
            notify("✅ No new messages found in the last 7 days.")
            return

        rows_to_add = []
        for message_info in all_messages:
            msg_id = message_info['id']
            if msg_id in existing_ids:
                continue

            msg = gmail_service.users().messages().get(userId='me', id=msg_id, format='metadata').execute()
            headers = msg.get('payload', {}).get('headers', [])
            
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
            date = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')
            
            label_ids = msg.get('labelIds', [])
            source = '1099 Email' # Default source
            if LABEL_ID_AEGIS_EMAIL in label_ids: source = 'Aegis Email'
            elif LABEL_ID_PERSONAL_EMAIL in label_ids: source = 'Personal Email'
            elif LABEL_ID_AEGIS_GV in label_ids: source = 'Google Voice - Aegis'
            elif LABEL_ID_1099_GV in label_ids: source = 'Google Voice - 1099'

            rows_to_add.append([msg_id, date, source, sender, subject, 'Needs Review', ''])
            existing_ids.add(msg_id) # Add to set to prevent re-adding in same run

        if rows_to_add:
            worksheet.append_rows(rows_to_add, value_input_option='USER_ENTERED')
            notify(f"📥 Fetched and logged {len(rows_to_add)} new communication(s)!")
        else:
            notify("✅ No new communications to log (all recent emails are already in the sheet).")

    except HttpError as error:
        error_msg = f'An API error occurred: {error}'
        if cli_mode: print(error_msg)
        else: st.error(error_msg)
    except Exception as e:
        error_msg = f'An unexpected error occurred: {e}'
        if cli_mode: print(error_msg)
        else: st.error(error_msg)


def main():
    """Handles authentication and runs the fetch process in command-line mode."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('.streamlit/credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    gspread_client = gspread.authorize(creds)
    gmail_service = build('gmail', 'v1', credentials=creds)
    
    run_fetch_communications(gmail_service, gspread_client, cli_mode=True)

if __name__ == '__main__':
    main()