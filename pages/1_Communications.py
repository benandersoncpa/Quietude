import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta, timezone
import base64
import streamlit.components.v1 as components
from email.utils import parsedate_to_datetime
import json
import asyncio
import httpx
import plan_my_day as planner

# --- CORRECTED IMPORTS ---
# All data and action functions are now imported from the centralized quietude.py library
from quietude import (
    authenticate_google,
    fetch_sheet_data,
    fetch_message_body,
    create_task,
    archive_message,
    update_task_status,
    set_task_waiting,
    snooze_task,
    reassign_task,
    add_note_to_task,
    get_current_focus_info,
    SPREADSHEET_ID,
    COMMUNICATIONS_SHEET_NAME,
    TASKS_SHEET_NAME,
    KNOWLEDGE_BASE_SHEET_NAME,
    COMPLETE_LABEL_ID
)


# --- CONFIGURATION (UI-specific) ---
EMAIL_ALIASES = st.secrets["EMAIL_ALIASES"]


# --- All local function definitions have been removed, as they are now imported ---

# --- Helper functions for this page's specific UI ---

def snooze_message(gspread_client, sheets_service, message_id, snooze_duration):
    """Updates the timestamp in the sheet to 'snooze' a message."""
    try:
        worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(COMMUNICATIONS_SHEET_NAME)
        cell = worksheet.find(message_id, in_column=1)
        if cell:
            future_time = datetime.now(timezone.utc) + snooze_duration
            future_time_str = future_time.isoformat() # Use ISO format for consistency
            
            # Use gspread's update_cell method directly for simplicity
            worksheet.update_cell(cell.row, 2, future_time_str) # Column B is Timestamp
            
            st.success(f"Message snoozed!")
            # Clear state and caches to force UI update
            st.session_state[f'show_snooze_{message_id}'] = False
            fetch_sheet_data.clear()
    except Exception as e:
        st.error(f"An error occurred while snoozing: {e}")

def report_spam(gspread_client, gmail_service, message_id):
    """Marks a message as spam in Gmail and archives it in the sheet."""
    try:
        # Move to Spam and remove from Inbox in Gmail
        body = {'addLabelIds': ['SPAM'], 'removeLabelIds': ['INBOX']}
        gmail_service.users().messages().modify(userId='me', id=message_id, body=body).execute()
        
        # Update status in the communications sheet
        comm_worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(COMMUNICATIONS_SHEET_NAME)
        cell = comm_worksheet.find(message_id, in_column=1)
        if cell:
            comm_worksheet.update_cell(cell.row, 6, "Archived") # Column F is Status
            
        st.warning(f"Message reported as spam.")
        fetch_sheet_data.clear()
    except Exception as e:
        st.error(f"Failed to report spam: {e}")

async def generate_responses(original_message, knowledge_base_text):
    prompt = f"""
    You are a helpful assistant for an introverted entrepreneur. Your goal is to draft clear, concise, and professional replies.
    Based on the user's internal knowledge base and the content of the incoming email, please generate three distinct response options.

    INTERNAL KNOWLEDGE BASE:
    ---
    {knowledge_base_text}
    ---

    INCOMING EMAIL:
    ---
    {original_message}
    ---

    Generate a JSON array of exactly three objects, each with a 'title' (string) and a 'body' (string) for each response option.
    Option 1: A direct and helpful answer.
    Option 2: A polite request for more information or clarification.
    Option 3: A brief acknowledgment that you've received the message and will follow up later.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "PASTE_YOUR_API_KEY_HERE":
        return [{"title": "Configuration Error", "body": "Please add your Gemini API Key to the script configuration."}]
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
        }
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            if result.get('candidates'):
                response_text = result['candidates'][0]['content']['parts'][0]['text']
                return json.loads(response_text)
            else:
                return [{"title": "API Error", "body": f"The API returned an unexpected response: {result}"}]
        except httpx.HTTPStatusError as e:
            return [{"title": "API HTTP Error", "body": f"Request failed with status {e.response.status_code}: {e.response.text}"}]
        except Exception as e:
            return [{"title": "Error", "body": f"Could not generate AI responses: {e}"}]

def send_reply(gspread_client, gmail_service, details):
    """Sends a reply email and archives the original message."""
    try:
        original_msg = gmail_service.users().messages().get(userId='me', id=details['message_id']).execute()
        headers = original_msg['payload']['headers']
        original_subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        original_message_id_header = next((h['value'] for h in headers if h['name'].lower() == 'message-id'), '')
        
        from_alias = EMAIL_ALIASES.get(details['source'], EMAIL_ALIASES['Primary Inbox'])

        message = MIMEText(details['body'])
        message['to'] = details['recipient']
        message['from'] = from_alias
        message['subject'] = f"Re: {original_subject}"
        message['In-Reply-To'] = original_message_id_header
        message['References'] = original_message_id_header

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_body = {'raw': encoded_message, 'threadId': original_msg['threadId']}
        gmail_service.users().messages().send(userId='me', body=send_body).execute()

        # Archive the message in Gmail and the sheet
        archive_message(gspread_client, gmail_service, details['message_id'])

        st.success(f"Reply sent to {details['recipient']}!")
        
        # Clear UI state
        st.session_state[f'show_reply_form_{details["message_id"]}'] = False
        if f'selected_reply_{details["message_id"]}' in st.session_state:
            del st.session_state[f'selected_reply_{details["message_id"]}']
        fetch_sheet_data.clear()

    except Exception as e:
        st.error(f"Failed to send reply: {e}")


# --- MAIN UI ---
st.set_page_config(layout="wide")
st.title("📡 Quietude Command Center")
st.link_button("🚀 Open Quietude OS Google Sheet", "https://docs.google.com/spreadsheets/d/1o5LmRv4MUQmO84bouTiBdqFzZu-lqx8V_YDVSBSsi2c/edit?gid=0#gid=0")

# Initialize UI state variables
if 'current_task_index' not in st.session_state:
    st.session_state.current_task_index = 0

# Authenticate and get services
gspread_client, gmail_service, sheets_service = authenticate_google()

# Load daily plan if not already loaded
if 'daily_schedule' not in st.session_state:
    with st.spinner("Generating today's plan..."):
        st.session_state.daily_schedule, st.session_state.fixed_events = planner.generate_schedule()

current_block, tasks_in_block = get_current_focus_info(st.session_state.daily_schedule)


if current_block:
    st.header(f"🎯 Deep Work session in progress until {current_block['end'].strftime('%I:%M %p')}.")
    st.markdown("---")
    
    if tasks_in_block:
        if st.session_state.current_task_index >= len(tasks_in_block):
            st.success("All tasks for this focus block are complete!")
        else:
            # Display current task
            task_info = tasks_in_block[st.session_state.current_task_index]
            task_id = task_info['task_id']
            
            tasks_df = fetch_sheet_data(gspread_client, TASKS_SHEET_NAME)
            current_task_df = tasks_df[tasks_df['TaskID'] == task_id]

            if not current_task_df.empty:
                task = current_task_df.iloc[0]
                st.subheader("Current Task:")
                st.title(f"✅ {task['Task Name']} ({task['Client']})")
                
                col1, col2, col3 = st.columns(3)
                # Call the centralized update_task_status function
                if col1.button("Mark as Completed", use_container_width=True, type="primary"):
                    # 1. Update the task in Google Sheets
                    update_task_status(gspread_client, task_id, "Done")
                    
                    # 2. CRITICAL: Regenerate the schedule to find the next dependent task
                    # This is the only way to "add the next step" to the workflow.
                    with st.spinner("Finding next task..."):
                        schedule, events = planner.generate_schedule(schedule_already_generated=True)
                        st.session_state.daily_schedule = schedule
                        st.session_state.fixed_events = events
                    
                    # 3. Reset the task index to start from the top of the new plan
                    st.session_state.current_task_index = 0
                    
                    # 4. Clear data caches and rerun the app to show the new task
                    fetch_sheet_data.clear()
                    st.rerun()     

                if col2.button("Waiting for Client", use_container_width=True):
                    set_task_waiting(gspread_client, task_id)
                    st.session_state.current_task_index += 1
                    fetch_sheet_data.clear() 
                    st.rerun()

                if col3.button("Snooze Task", use_container_width=True):
                    # For snoozing, we just push the start date and re-plan
                    snooze_task(gspread_client, task_id, timedelta(days=1))
                    st.session_state.current_task_index += 1
                    fetch_sheet_data.clear()
                    st.rerun()
            else:
                st.info("Advancing to the next task...")
                st.session_state.current_task_index += 1
                st.rerun()
    else:
        st.info("This is a scheduled focus block. No specific tasks were assigned. Use this time for deep work.")
else:
    # --- INBOX VIEW ---
    st.header("📥 Inbox")
    communications_df = fetch_sheet_data(gspread_client, COMMUNICATIONS_SHEET_NAME)

    review_df = pd.DataFrame()
    if not communications_df.empty and 'Status' in communications_df.columns:
        review_df = communications_df[communications_df['Status'] == 'Needs Review'].copy()

    # Filter for visible (non-snoozed) items
    now_aware = datetime.now(timezone.utc)
    visible_rows = []
    if not review_df.empty:
        for _, row in review_df.iterrows():
            try:
                # Timestamps should be parsed as timezone-aware
                timestamp_dt = pd.to_datetime(row['Timestamp']).tz_convert('UTC')
                if timestamp_dt <= now_aware:
                    visible_rows.append(row)
            except (TypeError, ValueError):
                # If timestamp is invalid, show it so it can be handled
                visible_rows.append(row)
    
    visible_df = pd.DataFrame(visible_rows)
    if not visible_df.empty:
        visible_df = visible_df.sort_values(by='Timestamp', ascending=False).reset_index(drop=True)


    if visible_df.empty:
        st.success("✅ All communications processed! Your inbox is clear.")
    else:
        st.info(f"You have {len(visible_df)} new communication(s) to review.")
        for index, row in visible_df.iterrows():
            msg_id = row.get('MessageID')
            expander_label = f"**From:** {str(row.get('Sender', 'N/A'))}  |  **Subject:** {str(row.get('Subject/Snippet', ''))[:60]}..."
            
            with st.expander(expander_label):
                main_col, action_col = st.columns([5, 1])
                with main_col:
                    message_body_html = fetch_message_body(gmail_service, msg_id, clean=False)
                    components.html(message_body_html, height=400, width=1200, scrolling=True)

                with action_col:
                    st.write("**Actions**")
                    if st.button("Respond", key=f"respond_{msg_id}", use_container_width=True):
                        st.session_state[f'show_reply_form_{msg_id}'] = True; st.rerun()
                    if st.button("Create Task", key=f"task_{msg_id}", use_container_width=True):
                        st.session_state[f'show_task_form_{msg_id}'] = True; st.rerun()
                    if st.button("Archive", key=f"archive_{msg_id}", use_container_width=True):
                        archive_message(gspread_client, gmail_service, msg_id); st.rerun()
                    st.markdown("---")

                    if st.button("Snooze", key=f"remind_{msg_id}", use_container_width=True):
                        snooze_message(gspread_client, sheets_service, msg_id, timedelta(days=1)); st.rerun()
                    if st.button("Report Spam", key=f"spam_{msg_id}", use_container_width=True):
                        report_spam(gspread_client, gmail_service, msg_id); st.rerun()

                # --- FORMS (Reply, Create Task) ---
                if st.session_state.get(f'show_reply_form_{msg_id}', False):
                    st.write("---")
                    st.subheader("AI-Generated Replies")
                    message_body_clean = fetch_message_body(gmail_service, msg_id, clean=True)
                    knowledge_df = fetch_sheet_data(gspread_client, KNOWLEDGE_BASE_SHEET_NAME)
                    knowledge_base_text = "\n".join([f"- {row['Topic']}: {row['Information']}" for _, row in knowledge_df.iterrows()])

                    if f'ai_responses_{msg_id}' not in st.session_state:
                        with st.spinner("Generating smart replies..."):
                            st.session_state[f'ai_responses_{msg_id}'] = asyncio.run(generate_responses(message_body_clean, knowledge_base_text))

                    if f'ai_responses_{msg_id}' in st.session_state:
                        for i, response in enumerate(st.session_state[f'ai_responses_{msg_id}']):
                            if st.button(response['title'], key=f"use_resp_{i}_{msg_id}"):
                                st.session_state[f'selected_reply_{msg_id}'] = response['body']
                                st.rerun()
                    
                    if f'selected_reply_{msg_id}' in st.session_state:
                        with st.form(key=f"reply_form_{msg_id}"):
                            st.write("##### Edit and Send")
                            reply_text = st.text_area("Response", value=st.session_state[f'selected_reply_{msg_id}'], height=200)
                            if st.form_submit_button("Send Reply"):
                                send_reply(gspread_client, gmail_service, {
                                    "message_id": msg_id, "recipient": row.get('Sender'),
                                    "source": row.get('Source'), "body": reply_text
                                }); st.rerun()

                if st.session_state.get(f'show_task_form_{msg_id}', False):
                    with st.form(key=f"task_form_{msg_id}"):
                        st.subheader("Create New Task")
                        task_name = st.text_input("Task Name", value=row.get('Subject/Snippet', ''))
                        due_date = st.date_input("Due Date", value=datetime.now() + timedelta(days=7))
                        est_time = st.number_input("Est. Time (minutes)", min_value=5, step=5, value=30)
                        enjoyment = st.slider("Enjoyment (1-5)", 1, 5, 3)
                        importance = st.slider("Importance (1-5)", 1, 5, 3)

                        if st.form_submit_button("Save Task"):
                            create_task(gspread_client, {
                                "message_id": msg_id, "name": task_name, "start_date": datetime.now(),
                                "due_date": due_date, "est_time": est_time, "enjoyment": enjoyment,
                                "importance": importance, "link": f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
                            })
                            st.session_state[f'show_task_form_{msg_id}'] = False
                            st.rerun()