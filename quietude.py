import streamlit as st
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import os.path
from datetime import datetime, timedelta, timezone
import base64
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import parsedate_to_datetime
import plan_my_day as planner
import traceback
import json

# --- CONFIGURATION ---
COMMUNICATIONS_SHEET_NAME = 'Communications'
TASKS_SHEET_NAME = 'Tasks'
USERS_SHEET_NAME = 'Users'
KNOWLEDGE_BASE_SHEET_NAME = 'Knowledge_Base'
AI_FEEDBACK_SHEET_NAME = 'AI_Feedback'
ACTIVE_WORKFLOWS_SHEET_NAME = "Active_Workflows"
WORKFLOW_STEPS_SHEET_NAME = "Workflow_Steps"
WORKFLOW_TEMPLATES_SHEET_NAME = "Workflow_Templates"
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
COMPLETE_LABEL_ID = st.secrets["COMPLETE_LABEL_ID"]
LABEL_ID_AEGIS_EMAIL = st.secrets["LABEL_ID_AEGIS_EMAIL"]
LABEL_ID_PERSONAL_EMAIL = st.secrets["LABEL_ID_PERSONAL_EMAIL"]
LABEL_ID_AEGIS_GV = st.secrets["LABEL_ID_AEGIS_GV"]
LABEL_ID_1099_GV = st.secrets["LABEL_ID_1099_GV"]

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/calendar.readonly'
]

# --- AUTHENTICATION FUNCTION (SHARED) ---
@st.cache_resource
def authenticate_google():
    # Try to load from credentials.json file first (for local development)
    # Fall back to Streamlit secrets for cloud deployment
    try:
        if os.path.exists('.streamlit/credentials.json'):
            creds = Credentials.from_service_account_file('.streamlit/credentials.json', scopes=SCOPES)
        else:
            client_secrets_dict = json.loads(st.secrets["GOOGLE_CLIENT_SECRETS"])
            creds = Credentials.from_service_account_info(client_secrets_dict, scopes=SCOPES)
    except Exception as e:
        st.error(f"Authentication failed: {e}")
        raise
    gspread_client = gspread.authorize(creds)
    gmail_service = build('gmail', 'v1', credentials=creds)
    sheets_service = build('sheets', 'v4', credentials=creds)
    return gspread_client, gmail_service, sheets_service

def clear_auth_cache():
    """Force re-authentication by clearing the cached credentials."""
    st.cache_resource.clear()

# --- DATA & ACTION FUNCTIONS ---
@st.cache_data(ttl=60)
def fetch_sheet_data(_client, sheet_name):
    try:
        worksheet = _client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
        data = worksheet.get_all_values()
        if len(data) > 1:
            df = pd.DataFrame(data[1:], columns=data[0])
            return df
        return pd.DataFrame(columns=data[0] if data else [])
    except Exception as e:
        st.error(f"Failed to fetch data from '{sheet_name}': {e}")
        return pd.DataFrame()

def update_task_status(gspread_client, task_id, new_status):
    """
    Updates a task's status. If marked 'Done' and part of a workflow:
    - Advances the Active_Workflow to the next step
    - Calculates and updates dates for the next task in the workflow
    - Checks if this step triggers a new workflow
    """
    try:
        tasks_df = fetch_sheet_data(gspread_client, TASKS_SHEET_NAME)
        active_workflows_df = fetch_sheet_data(gspread_client, ACTIVE_WORKFLOWS_SHEET_NAME)
        workflow_steps_df = fetch_sheet_data(gspread_client, WORKFLOW_STEPS_SHEET_NAME)

        task_row_df = tasks_df[tasks_df['TaskID'].astype(str) == str(task_id)]
        if task_row_df.empty:
            st.error(f"Error: Could not find TaskID '{task_id}'. Please refresh.")
            return

        tasks_sheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        cell = tasks_sheet.find(task_id, in_column=1)
        if not cell:
            st.error(f"Could not find cell for TaskID {task_id} in the sheet.")
            return
            
        # Update the task status
        tasks_sheet.update_cell(cell.row, tasks_df.columns.get_loc('Status') + 1, new_status)
        if new_status == 'Done':
            try:
                date_completed_col = tasks_df.columns.get_loc('Date_Completed') + 1
                tasks_sheet.update_cell(cell.row, date_completed_col, datetime.now().strftime('%Y-%m-%d'))
            except KeyError:
                pass
        
        st.toast(f"Task '{task_row_df.iloc[0]['Task Name']}' updated to '{new_status}'.")

        if new_status != 'Done':
            st.cache_data.clear()
            return

        completed_task = task_row_df.iloc[0]
        active_workflow_id = completed_task.get('ActiveWorkflowID')

        if not (active_workflow_id and pd.notna(active_workflow_id) and str(active_workflow_id).strip()):
            st.info("✅ Task is not part of a workflow. No further actions taken.")
            st.cache_data.clear()
            return

        # --- WORKFLOW LOGIC FOR COMPLETED TASK ---
        
        active_workflow = active_workflows_df[active_workflows_df['ActiveWorkflowID'] == active_workflow_id]
        if active_workflow.empty:
            st.warning(f"⚠️ Task has ActiveWorkflowID '{active_workflow_id}', but this workflow was not found in the Active_Workflows sheet.")
            st.cache_data.clear()
            return
        
        # Get workflow details
        aw_sheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(ACTIVE_WORKFLOWS_SHEET_NAME)
        aw_cell = aw_sheet.find(active_workflow_id, in_column=1)
        current_step = int(active_workflow.iloc[0].get('Current_Step', 0))
        template_id = active_workflow.iloc[0].get('WorkflowID')
        
        # Get all steps for this workflow
        all_steps = workflow_steps_df[workflow_steps_df['WorkflowID'] == template_id].copy()
        all_steps['Step_Number'] = pd.to_numeric(all_steps['Step_Number'], errors='coerce')
        all_steps = all_steps.sort_values('Step_Number')
        
        total_steps = len(all_steps)

        # 1. ADVANCE THE WORKFLOW AND UPDATE NEXT TASK DATES
        if current_step >= total_steps:
            # All steps complete!
            aw_sheet.update_cell(aw_cell.row, active_workflows_df.columns.get_loc('Status') + 1, 'Done')
            st.success(f"🎉 Workflow {active_workflow_id} completed! All {total_steps} tasks finished.")
        else:
            # Advance to next step
            next_step_num = current_step + 1
            aw_sheet.update_cell(aw_cell.row, active_workflows_df.columns.get_loc('Current_Step') + 1, next_step_num)
            
            # Find the next task in the workflow (should already exist)
            next_tasks = tasks_df[
                (tasks_df['ActiveWorkflowID'] == active_workflow_id) &
                (pd.to_numeric(tasks_df['Workflow_Step_Number'], errors='coerce') == next_step_num)
            ]
            
            if not next_tasks.empty:
                # Update the existing next task with calculated dates
                next_step_df = all_steps[all_steps['Step_Number'] == next_step_num]
                if not next_step_df.empty:
                    next_step = next_step_df.iloc[0]
                    
                    # Calculate dates based on relative offsets from NOW
                    today = datetime.now()
                    relative_start_days = int(next_step.get('Relative_Start_Date', 0))
                    relative_due_days = int(next_step.get('Relative_Due_Date', 1))
                    
                    start_date_obj = today + timedelta(days=relative_start_days)
                    due_date_obj = start_date_obj + timedelta(days=relative_due_days)
                    
                    start_date_str = start_date_obj.strftime('%Y-%m-%d')
                    due_date_str = due_date_obj.strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Find and update the next task
                    next_task_id = next_tasks.iloc[0]['TaskID']
                    next_task_cell = tasks_sheet.find(next_task_id, in_column=1)
                    
                    if next_task_cell:
                        try:
                            start_date_col = tasks_df.columns.get_loc('Start Date') + 1
                            due_date_col = tasks_df.columns.get_loc('Due Date') + 1
                            tasks_sheet.update_cell(next_task_cell.row, start_date_col, start_date_str)
                            tasks_sheet.update_cell(next_task_cell.row, due_date_col, due_date_str)
                            st.info(f"✅ Dates calculated for next task: '{next_tasks.iloc[0]['Task Name']}'")
                        except KeyError:
                            st.warning("Could not update dates for next task (column not found)")
            else:
                # This shouldn't happen if workflow was created correctly, but handle gracefully
                st.warning(f"⚠️ Could not find next task (Step {next_step_num}) for this workflow. The workflow may be incomplete.")

        # 2. CHECK IF COMPLETED STEP TRIGGERS A NEW WORKFLOW
        current_step_details = all_steps[all_steps['Step_Number'] == current_step]
        
        if not current_step_details.empty:
            new_workflow_template_id = current_step_details.iloc[0].get('Next_WorkflowID_On_Completion')
            if new_workflow_template_id and pd.notna(new_workflow_template_id) and str(new_workflow_template_id).strip():
                st.success(f"🚀 Triggering new workflow: '{new_workflow_template_id}'!")
                # Create new workflow with all its tasks
                aw_headers = aw_sheet.row_values(1)
                new_active_workflow_id = f"AWF-{uuid.uuid4().hex[:6].upper()}"
                new_active_workflow = {
                    'ActiveWorkflowID': new_active_workflow_id,
                    'WorkflowID': new_workflow_template_id,
                    'Client': completed_task.get('Client'),
                    'Start_Date': datetime.now().strftime('%Y-%m-%d'),
                    'Status': 'In Progress',
                    'Current_Step': 1
                }
                aw_sheet.append_row([new_active_workflow.get(h, '') for h in aw_headers], value_input_option='USER_ENTERED')
                st.info(f"Added new Active_Workflow with ID: {new_active_workflow_id}")
                
                # Create all tasks for the new workflow
                new_workflow_steps = workflow_steps_df[workflow_steps_df['WorkflowID'] == new_workflow_template_id].copy()
                new_workflow_steps['Step_Number'] = pd.to_numeric(new_workflow_steps['Step_Number'], errors='coerce')
                new_workflow_steps = new_workflow_steps.sort_values('Step_Number')
                
                today = datetime.now()
                for _, new_step in new_workflow_steps.iterrows():
                    step_number = int(new_step['Step_Number'])
                    
                    if step_number == 1:
                        relative_start_days = int(new_step.get('Relative_Start_Date', 0))
                        relative_due_days = int(new_step.get('Relative_Due_Date', 1))
                        start_date = today + timedelta(days=relative_start_days)
                        due_date = start_date + timedelta(days=relative_due_days)
                        start_date_str = start_date.strftime('%Y-%m-%d')
                        due_date_str = due_date.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        start_date_str = ''
                        due_date_str = ''
                    
                    new_task = {
                        'TaskID': f"TSK-{uuid.uuid4().hex[:6].upper()}",
                        'Task Name': new_step.get('Step_Name'),
                        'Status': 'To Do',
                        'Client': completed_task.get('Client'),
                        'Start Date': start_date_str,
                        'Due Date': due_date_str,
                        'ActiveWorkflowID': new_active_workflow_id,
                        'Estimated Time': new_step.get('Est_Time', 0),
                        'Enjoyment': new_step.get('Enjoyment', 3),
                        'Importance': new_step.get('Importance', 3),
                        'Workflow_Step_Number': step_number
                    }
                    
                    task_row = [new_task.get(h, '') for h in aw_headers if h in new_task]
                    task_headers = tasks_sheet.row_values(1)
                    task_row = [new_task.get(h, '') for h in task_headers]
                    tasks_sheet.append_row(task_row, value_input_option='USER_ENTERED')
                
                st.success(f"✅ Created {len(new_workflow_steps)} tasks for new workflow!")
        
        st.cache_data.clear()

    except Exception as e:
        st.error("🛑 An unexpected error occurred during the workflow logic.")
        st.code(traceback.format_exc())


def run_fetch_communications(gmail_service, gspread_client, max_retries=2):
    """
    Fetches recent emails with robust error handling and retry logic.
    If fetch fails, returns gracefully without crashing the app.
    Handles 400 and 403 errors gracefully.
    """
    import time
    
    for attempt in range(max_retries):
        try:
            spreadsheet = gspread_client.open_by_key(SPREADSHEET_ID)
            worksheet = spreadsheet.worksheet(COMMUNICATIONS_SHEET_NAME)
            existing_ids = set(worksheet.col_values(1))
            all_messages = []
            page_token = None
            
            while True:
                try:
                    results = gmail_service.users().messages().list(
                        userId='me', 
                        q='in:inbox newer_than:7d', 
                        maxResults=500, 
                        pageToken=page_token
                    ).execute()
                    
                    messages = results.get('messages', [])
                    all_messages.extend(messages)
                    page_token = results.get('nextPageToken')
                    if not page_token: 
                        break
                        
                except HttpError as e:
                    error_code = e.resp.status
                    # Handle 400 (Precondition Failed) and 403 (Forbidden/Quota)
                    if error_code in [400, 403]:
                        if attempt < max_retries - 1:
                            wait_time = 2 ** (attempt + 1)  # 2s, 4s backoff
                            # On second attempt, suggest clearing cache
                            if attempt == 1:
                                st.info("💡 Try clicking 'Clear Cache' in the browser menu if this persists.")
                            st.warning(f"⚠️ Gmail API temporarily unavailable (Error {error_code}). Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})")
                            time.sleep(wait_time)
                            break  # Break inner loop to retry outer loop
                        else:
                            st.warning("⚠️ Gmail API unavailable. Using previously cached communications. Try refreshing the page or clearing your browser cache.")
                            return False
                    else:
                        raise
            
            if not all_messages:
                st.toast("✅ No new messages found in the last 7 days.")
                return True
            
            rows_to_add = []
            for message_info in all_messages:
                msg_id = message_info['id']
                if msg_id in existing_ids: 
                    continue
                    
                try:
                    msg = gmail_service.users().messages().get(
                        userId='me', 
                        id=msg_id, 
                        format='metadata'
                    ).execute()
                    
                    headers = msg.get('payload', {}).get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
                    sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
                    date = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')
                    label_ids = msg.get('labelIds', [])
                    
                    source = '1099 Email'
                    if LABEL_ID_AEGIS_EMAIL in label_ids: 
                        source = 'Aegis Email'
                    elif LABEL_ID_PERSONAL_EMAIL in label_ids: 
                        source = 'Personal Email'
                    elif LABEL_ID_AEGIS_GV in label_ids: 
                        source = 'Google Voice - Aegis'
                    elif LABEL_ID_1099_GV in label_ids: 
                        source = 'Google Voice - 1099'
                    
                    rows_to_add.append([msg_id, date, source, sender, subject, 'Needs Review', ''])
                    existing_ids.add(msg_id)
                    
                except HttpError as e:
                    # Skip individual messages that fail with 400/403
                    if e.resp.status in [400, 403]:
                        continue
                    else:
                        raise
            
            if rows_to_add:
                try:
                    worksheet.append_rows(rows_to_add, value_input_option='USER_ENTERED')
                    st.toast(f"📥 Fetched and logged {len(rows_to_add)} new communication(s)!")
                except Exception as e:
                    st.warning(f"⚠️ Fetched {len(rows_to_add)} emails but couldn't save all: {str(e)[:100]}")
            else:
                st.toast("✅ No new communications to log (all recent emails are already in the sheet).")
            
            return True
            
        except HttpError as error:
            error_code = error.resp.status
            if error_code in [400, 403] and attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                st.warning(f"⚠️ Gmail API temporarily unavailable (Error {error_code}). Retrying... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            else:
                st.warning(f"⚠️ Gmail API unavailable (Error {error_code}). Using cached communications. Try refreshing the page or clearing your browser cache.")
                return False
                
        except Exception as e:
            st.warning(f"⚠️ Could not fetch communications: {str(e)[:150]}. Using cached data instead.")
            return False
    
    return False

def fetch_message_body(_gmail_service, msg_id, clean=False):
    try:
        message = _gmail_service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        payload = message.get('payload', {})
        parts = payload.get('parts', [])
        body_data = ""
        mime_type_preference = 'text/plain' if clean else 'text/html'
        if parts:
            for part in parts:
                if part.get('mimeType') == mime_type_preference:
                    body_data = part.get('body', {}).get('data')
                    if body_data: break
            if not body_data:
                for part in parts:
                    if part.get('mimeType') in ['text/html', 'text/plain']:
                        body_data = part.get('body', {}).get('data')
                        if body_data: break
        else:
            body_data = payload.get('body', {}).get('data')
        if body_data:
            decoded_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
            if clean and ('<' in decoded_body and '>' in decoded_body):
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(decoded_body, "html.parser")
                return soup.get_text()
            return decoded_body
        return "Message body could not be loaded."
    except Exception as e:
        return f"Error fetching message body: {e}"

def create_task(gspread_client, task_details):
    """Creates a new task in the Tasks sheet. Does NOT handle communication updates."""
    try:
        tasks_worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        
        # Ensure date objects are formatted correctly
        start_date_str = task_details.get('start_date').strftime('%Y-%m-%d') if hasattr(task_details.get('start_date'), 'strftime') else str(task_details.get('start_date', ''))
        due_date_str = task_details.get('due_date').strftime('%Y-%m-%d') if hasattr(task_details.get('due_date'), 'strftime') else str(task_details.get('due_date', ''))

        new_task_row = [
            task_id,
            task_details.get('name', 'Unnamed Task'),
            task_details.get('client', ''),
            'To Do', # Status
            start_date_str,
            due_date_str,
            task_details.get('est_time', 30),
            task_details.get('enjoyment', 3),
            task_details.get('importance', 3),
            task_details.get('link', ''),
            task_details.get('workflow_id', ''),
            task_details.get('assignee', 'Ben Anderson'),
            task_details.get('notes', '')
        ]
        tasks_worksheet.append_row(new_task_row, value_input_option='USER_ENTERED')
        st.toast(f"✅ Task '{task_details.get('name')}' created successfully!")
    except Exception as e:
        st.error(f"Failed to create task: {e}")
        st.exception(e)


def start_workflow(gspread_client, gmail_service, workflow_details):
    """
    Starts a new workflow: adds ALL tasks to 'Tasks' sheet (not just the first one),
    creates a record in 'Active_Workflows', and archives the source email.
    
    Only the first task gets initial start/due dates.
    Subsequent tasks will have their dates calculated when the previous task is completed.
    """
    try:
        # 1. Fetch workflow steps
        steps_df = fetch_sheet_data(gspread_client, WORKFLOW_STEPS_SHEET_NAME)
        
        # 2. Get the workflow template ID and find all steps
        template_id = int(workflow_details['workflow_template_id'])
        workflow_steps = steps_df[steps_df['WorkflowID'] == template_id].copy()
        workflow_steps['Step_Number'] = pd.to_numeric(workflow_steps['Step_Number'], errors='coerce')
        workflow_steps = workflow_steps.sort_values('Step_Number')
        
        if workflow_steps.empty:
            st.error(f"Could not find any steps for Workflow ID {template_id}.")
            return

        # 3. Create the Active Workflow record
        active_workflows_sheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(ACTIVE_WORKFLOWS_SHEET_NAME)
        awf_id = f"AWF-{uuid.uuid4().hex[:6].upper()}"
        external_deadline = workflow_details.get('external_deadline')
        deadline_str = external_deadline.strftime('%Y-%m-%d') if external_deadline else ''

        new_workflow_row = [
            awf_id,
            template_id,
            workflow_details.get('client', ''),
            "In Progress",
            1,  # Current step starts at 1
            deadline_str
        ]
        active_workflows_sheet.append_row(new_workflow_row, value_input_option='USER_ENTERED')
        
        # 4. Create ALL tasks for this workflow
        tasks_sheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        task_headers = tasks_sheet.row_values(1)
        today = datetime.now()
        
        for idx, (_, step) in enumerate(workflow_steps.iterrows()):
            step_number = int(step['Step_Number'])
            
            if step_number == 1:
                # First task: calculate dates based on relative offsets
                relative_start_days = int(step.get('Relative_Start_Date', 0))
                relative_due_days = int(step.get('Relative_Due_Date', 1))
                start_date = today + timedelta(days=relative_start_days)
                due_date = start_date + timedelta(days=relative_due_days)
                start_date_str = start_date.strftime('%Y-%m-%d')
                due_date_str = due_date.strftime('%Y-%m-%d %H:%M:%S')
                task_status = 'To Do'
            else:
                # Subsequent tasks: leave dates empty, mark as pending
                start_date_str = ''
                due_date_str = ''
                task_status = 'To Do'  # Will remain 'To Do' until predecessor is completed
            
            new_task = {
                'TaskID': f"TSK-{uuid.uuid4().hex[:6].upper()}",
                'Task Name': step.get('Step_Name'),
                'Status': task_status,
                'Client': workflow_details.get('client', ''),
                'Start Date': start_date_str,
                'Due Date': due_date_str,
                'ActiveWorkflowID': awf_id,
                'Estimated Time': step.get('Est_Time', 0),
                'Enjoyment': step.get('Enjoyment', 3),
                'Importance': step.get('Importance', 3),
                'Workflow_Step_Number': step_number  # Track which step this task is
            }
            
            # Build the row in the correct order
            task_row = []
            for header in task_headers:
                task_row.append(new_task.get(header, ''))
            
            tasks_sheet.append_row(task_row, value_input_option='USER_ENTERED')
        
        st.success(f"Workflow started! Created {len(workflow_steps)} tasks for client '{workflow_details.get('client')}'.")
        
        # 5. Archive the original communication if it exists
        if workflow_details.get('message_id'):
            archive_message(gmail_service, gspread_client, workflow_details['message_id'])

    except Exception as e:
        st.error(f"Failed to start workflow: {e}")
        st.error(traceback.format_exc())


def archive_message(gmail_service, gspread_client, message_id):
    """Archives the message in Gmail and updates the status in the Google Sheet."""
    try:
        # 1. Update Gmail: Remove from Inbox and add the 'Complete' label
        gmail_service.users().messages().modify(
            userId='me', id=message_id, body={'addLabelIds': [COMPLETE_LABEL_ID], 'removeLabelIds': ['INBOX']}
        ).execute()

        # 2. Update Google Sheet
        worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(COMMUNICATIONS_SHEET_NAME)
        cell = worksheet.find(message_id, in_column=1)
        if cell:
            worksheet.update_cell(cell.row, 6, "Complete")
            st.toast("Message archived to 'Complete' and status updated in the sheet.")
        else:
            st.warning("Message archived in Gmail, but could not find the corresponding entry in the sheet to update.")
    except Exception as e:
        st.error(f"An error occurred during archival: {e}")

def send_reply(gspread_client, gmail_service, reply_details):
    """Sends an email reply and archives the original message."""
    try:
        original_message_id = reply_details['message_id']
        
        original_msg = gmail_service.users().messages().get(
            userId='me', id=original_message_id, format='metadata',
            metadataHeaders=['Subject', 'From', 'To', 'Message-ID', 'In-Reply-To', 'References']
        ).execute()

        original_headers = {h['name']: h['value'] for h in original_msg['payload']['headers']}
        
        message = MIMEMultipart()
        message['to'] = reply_details['recipient']
        message['from'] = original_headers.get('To')
        
        original_subject = original_headers.get('Subject', '')
        if not original_subject.lower().startswith('re:'):
            message['subject'] = f"Re: {original_subject}"
        else:
            message['subject'] = original_subject

        message['In-Reply-To'] = original_headers.get('Message-ID')
        message['References'] = original_headers.get('References', '') + ' ' + original_headers.get('Message-ID')

        message.attach(MIMEText(reply_details['body'], 'plain'))
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body = {'raw': raw_message}
        
        sent_message = gmail_service.users().messages().send(userId='me', body=body).execute()
        st.success(f"Reply sent successfully!")
        
        archive_message(gmail_service, gspread_client, original_message_id)

    except HttpError as error:
        st.error(f"An API error occurred while sending reply: {error}")
    except Exception as e:
        st.error(f"An unexpected error occurred while sending reply: {e}")
        st.error(traceback.format_exc())


def set_task_waiting(gspread_client, task_id):
    try:
        worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        cell = worksheet.find(task_id, in_column=1)
        if cell:
            start_date_str = worksheet.cell(cell.row, 5).value
            due_date_str = worksheet.cell(cell.row, 6).value
            new_start_date = (datetime.strptime(start_date_str, '%Y-%m-%d') + timedelta(weeks=1)).strftime('%Y-%m-%d')
            new_due_date = (datetime.strptime(due_date_str, '%Y-%m-%d') + timedelta(weeks=1)).strftime('%Y-%m-%d')
            worksheet.update_cell(cell.row, 4, "Waiting for Client")
            worksheet.update_cell(cell.row, 5, new_start_date)
            worksheet.update_cell(cell.row, 6, new_due_date)
            st.success("Task set to 'Waiting' and dates pushed.")
    except Exception as e:
        st.error(f"Failed to set task to waiting: {e}")

def snooze_task(gspread_client, task_id, snooze_duration):
    try:
        worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        cell = worksheet.find(task_id, in_column=1)
        if cell:
            new_start_date = (datetime.now() + snooze_duration).strftime('%Y-%m-%d')
            new_due_date = (datetime.now() + snooze_duration).strftime('%Y-%m-%d')
            worksheet.update_cell(cell.row, 5, new_start_date)
            worksheet.update_cell(cell.row, 6, new_due_date)
            st.success("Task snoozed.")
            st.session_state[f'show_task_snooze_{task_id}'] = False
    except Exception as e:
        st.error(f"Failed to snooze task: {e}")

def reassign_task(gspread_client, task_id, new_assignee):
    try:
        worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        cell = worksheet.find(task_id, in_column=1)
        if cell:
            worksheet.update_cell(cell.row, 12, new_assignee)
            st.success(f"Task reassigned to {new_assignee}.")
            st.session_state[f'show_reassign_{task_id}'] = False
    except Exception as e:
        st.error(f"Failed to reassign task: {e}")

def add_note_to_task(gspread_client, task_id, new_note):
    try:
        worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        cell = worksheet.find(task_id, in_column=1)
        if cell:
            existing_notes = worksheet.cell(cell.row, 13).value or ""
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            updated_notes = f"{existing_notes}\n[{timestamp}] {new_note}"
            worksheet.update_cell(cell.row, 13, updated_notes.strip())
            st.success("Note added.")
            st.session_state[f'show_add_note_{task_id}'] = False
    except Exception as e:
        st.error(f"Failed to add note: {e}")

def get_current_focus_info(schedule):
    now = datetime.now().astimezone()
    current_block = None
    tasks_in_block = []
    for item in schedule:
        if item.get('type') == 'focus' and item.get('start') <= now <= item.get('end'):
            current_block = item
            break
    if current_block:
        for item in schedule:
            if item.get('type') == 'task' and current_block.get('start') <= item.get('start') < current_block.get('end'):
                tasks_in_block.append(item)
    return current_block, tasks_in_block

def complete_legacy_workflow(gspread_client, active_workflow_id):
    """
    Completes legacy workflows that only have the first task created.
    Creates all remaining tasks in the workflow with empty dates.
    """
    try:
        active_workflows_df = fetch_sheet_data(gspread_client, ACTIVE_WORKFLOWS_SHEET_NAME)
        workflow_steps_df = fetch_sheet_data(gspread_client, WORKFLOW_STEPS_SHEET_NAME)
        tasks_df = fetch_sheet_data(gspread_client, TASKS_SHEET_NAME)
        
        active_workflow = active_workflows_df[active_workflows_df['ActiveWorkflowID'] == active_workflow_id]
        if active_workflow.empty:
            st.error(f"Workflow {active_workflow_id} not found.")
            return False
        
        template_id = active_workflow.iloc[0].get('WorkflowID')
        workflow_steps = workflow_steps_df[workflow_steps_df['WorkflowID'] == template_id].copy()
        workflow_steps['Step_Number'] = pd.to_numeric(workflow_steps['Step_Number'], errors='coerce')
        workflow_steps = workflow_steps.sort_values('Step_Number')
        
        # Find which steps already have tasks
        existing_tasks = tasks_df[tasks_df['ActiveWorkflowID'] == active_workflow_id]
        existing_step_numbers = set(pd.to_numeric(existing_tasks.get('Workflow_Step_Number', []), errors='coerce').dropna())
        
        # Create tasks for missing steps
        tasks_sheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        task_headers = tasks_sheet.row_values(1)
        client = active_workflow.iloc[0].get('Client', '')
        
        tasks_created = 0
        for _, step in workflow_steps.iterrows():
            step_number = int(step['Step_Number'])
            if step_number not in existing_step_numbers:
                # Create this task
                new_task = {
                    'TaskID': f"TSK-{uuid.uuid4().hex[:6].upper()}",
                    'Task Name': step.get('Step_Name'),
                    'Status': 'To Do',
                    'Client': client,
                    'Start Date': '',  # Will be calculated when previous task completes
                    'Due Date': '',    # Will be calculated when previous task completes
                    'ActiveWorkflowID': active_workflow_id,
                    'Estimated Time': step.get('Est_Time', 0),
                    'Enjoyment': step.get('Enjoyment', 3),
                    'Importance': step.get('Importance', 3),
                    'Workflow_Step_Number': step_number
                }
                
                task_row = [new_task.get(h, '') for h in task_headers]
                tasks_sheet.append_row(task_row, value_input_option='USER_ENTERED')
                tasks_created += 1
        
        if tasks_created > 0:
            st.success(f"✅ Added {tasks_created} missing task(s) to workflow {active_workflow_id}")
            st.cache_data.clear()
            return True
        else:
            st.info("✅ Workflow is already complete with all tasks created.")
            return False
            
    except Exception as e:
        st.error(f"Failed to complete legacy workflow: {e}")
        return False

st.set_page_config(
    page_title="Quietude OS",
    page_icon="🧘",
    layout="wide"
)

if 'daily_schedule' not in st.session_state:
    with st.spinner("Preparing your day..."):
        st.session_state.daily_schedule, st.session_state.fixed_events = planner.generate_schedule()