import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from dateutil import parser as date_parser
import pytz # For robust timezone handling

# Import shared functions from the centralized quietude.py library
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
    send_reply,
    start_workflow, # Import the new function
    COMMUNICATIONS_SHEET_NAME,
    TASKS_SHEET_NAME,
    USERS_SHEET_NAME,
    WORKFLOW_TEMPLATES_SHEET_NAME,
    run_fetch_communications
)
# Import the planner to get today's schedule
import plan_my_day as planner

# --- PAGE CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Command Center")
GSHEET_URL = "https://docs.google.com/spreadsheets/d/1o5LmRv4MUQmO84bouTiBdqFzZu-lqx8V_YDVSBSsi2c/edit?gid=2070779982#gid=2070779982"
st.link_button("🚀 Open Quietude OS Google Sheet", GSHEET_URL)
st.title("🎯 Command Center")
st.markdown("Your single next action to move the needle.")

# --- BATCHING CONFIGURATION ---
COMM_BATCH_SIZE = 20 # Number of communications to fetch at a time

# --- HELPER FUNCTIONS ---
def get_response_deadline(timestamp):
    """Calculates the 24-hour response deadline, skipping weekends."""
    deadline = timestamp + timedelta(days=1)
    if deadline.weekday() == 5: deadline += timedelta(days=2)
    elif deadline.weekday() == 6: deadline += timedelta(days=1)
    return deadline

# --- CORE LOGIC ---
def fetch_and_prepare_action_batch(_gspread_client):
    """Fetches ALL actionable items, sorts them by deadline, and queues the top 20."""
    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    today = now.date()
    all_action_items = []

    # 1. Fetch ALL communications needing review
    comms_df = fetch_sheet_data(_gspread_client, COMMUNICATIONS_SHEET_NAME)
    needs_review = comms_df[comms_df['Status'] == 'Needs Review'].copy()
    
    if not needs_review.empty:
        # Parse timestamps safely
        needs_review['parsed_timestamp'] = needs_review['Timestamp'].apply(
            lambda x: date_parser.parse(x) if isinstance(x, str) else pd.NaT
        )
        needs_review.dropna(subset=['parsed_timestamp'], inplace=True)
        
        def make_aware(dt):
            if dt.tzinfo is None: return pytz.UTC.localize(dt).astimezone(local_tz)
            return dt.astimezone(local_tz)

        needs_review['parsed_timestamp'] = needs_review['parsed_timestamp'].apply(make_aware)
        needs_review['response_deadline'] = needs_review['parsed_timestamp'].apply(get_response_deadline)
        
        # Add ALL emails to the pool
        for _, row in needs_review.iterrows():
            all_action_items.append({
                'type': 'communication', 
                'deadline': row['response_deadline'], 
                'data': row.to_dict()
            })

    # 2. Fetch ALL actionable tasks
    tasks_df = fetch_sheet_data(_gspread_client, TASKS_SHEET_NAME)
    actionable_tasks = tasks_df[tasks_df['Status'].isin(['To Do','In Drafts'])].copy()
    
    # Clean start date for filtering (tasks that haven't started shouldn't appear yet)
    actionable_tasks['Start Date'] = pd.to_datetime(actionable_tasks['Start Date'], errors='coerce').dt.date
    actionable_tasks = actionable_tasks[actionable_tasks['Start Date'] <= today]

    if not actionable_tasks.empty:
        for _, task_details in actionable_tasks.iterrows():
            due_date_str = task_details.get('Due Date')
            task_deadline = None
            
            if due_date_str and isinstance(due_date_str, str):
                try:
                    # Logic: If string is short (e.g. "2023-01-01"), assume no time and add 12:01 AM
                    if len(due_date_str.strip()) <= 10:
                        due_date_str += " 00:01:00"
                    
                    # Parse the string (handles both "YYYY-MM-DD" and "YYYY-MM-DD HH:MM:SS")
                    dt = date_parser.parse(due_date_str)
                    
                    # Ensure timezone awareness
                    if dt.tzinfo is None:
                        task_deadline = dt.replace(tzinfo=local_tz)
                    else:
                        task_deadline = dt.astimezone(local_tz)
                        
                    all_action_items.append({
                        'type': 'task', 
                        'deadline': task_deadline, 
                        'data': task_details.to_dict()
                    })
                except (ValueError, TypeError):
                    pass # Skip tasks with completely broken dates

    # 3. Sort the combined list by deadline
    # Sort by deadline (earliest first)
    sorted_items = sorted(all_action_items, key=lambda x: x['deadline'])
    
    # 4. Slice the top N items (Chronological mix of tasks and emails)
    # Using the global constant COMM_BATCH_SIZE (now effectively "TOTAL_BATCH_SIZE")
    st.session_state.action_queue = sorted_items[:COMM_BATCH_SIZE]

# --- UI RENDERING ---
try:
    gspread_client, gmail_service, _ = authenticate_google()
    # Fetch data for forms
    users_df = fetch_sheet_data(gspread_client, USERS_SHEET_NAME)
    workflows_df = fetch_sheet_data(gspread_client, WORKFLOW_TEMPLATES_SHEET_NAME)

    # Prepare lists for selectboxes
    user_list = users_df['Users'].tolist() if not users_df.empty else []
    workflow_options = {name: str(w_id) for name, w_id in zip(workflows_df['Workflow Name'], workflows_df['WorkflowID'])} if not workflows_df.empty else {}


    if 'action_queue' not in st.session_state or st.button("🔄 Fetch New Batch"):
        with st.spinner("Fetching a new batch of actions..."):
            run_fetch_communications(gmail_service, gspread_client)
            fetch_and_prepare_action_batch(gspread_client)

    if not st.session_state.get('action_queue'):
        st.success("🎉 Batch complete! Fetch a new batch when you're ready.")
        st.balloons()
    else:
        next_item = st.session_state.action_queue[0]
        
        # --- RENDER COMMUNICATION UI ---
        if next_item['type'] == 'communication':
            comm = next_item['data']
            msg_id = comm['MessageID']
            st.subheader(f"Next Up: Respond to Communication")
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**From:** {comm.get('Sender')}")
                    st.write(f"**Subject:** {comm.get('Subject/Snippet')}")
                    st.error(f"**Respond by:** {comm['response_deadline'].strftime('%b %d, %Y at %I:%M %p')}")
                with col2:
                    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
                    st.link_button("✉️ Open in Gmail", gmail_link, use_container_width=True)

                with st.spinner("Loading message..."):
                    body_html = fetch_message_body(gmail_service, msg_id)
                    st.components.v1.html(body_html, height=400, width=1200, scrolling=True)
            st.markdown("---")
            st.subheader("Actions")
            
            # Action Buttons
            col1, col2, col3, col4, col5= st.columns(5)
            if col1.button("Reply", key=f"reply_{msg_id}", use_container_width=True):
                st.session_state[f'show_reply_form_{msg_id}'] = not st.session_state.get(f'show_reply_form_{msg_id}', False)
            if col2.button("Start Workflow", key=f"workflow_{msg_id}", use_container_width=True):
                st.session_state[f'show_workflow_form_{msg_id}'] = not st.session_state.get(f'show_workflow_form_{msg_id}', False)
            if col3.button("Create Task", key=f"task_{msg_id}", use_container_width=True):
                st.session_state[f'show_task_form_{msg_id}'] = not st.session_state.get(f'show_task_form_{msg_id}', False)
            if col4.button("Archive", key=f"archive_{msg_id}", use_container_width=True, type="primary"):
                archive_message(gmail_service, gspread_client, msg_id)
                if st.session_state.action_queue:
                    st.session_state.action_queue = st.session_state.action_queue[1:]
                st.rerun()
            if col5.button("Unsubscribe/Report Spam", key=f"report_spam_{msg_id}", use_container_width=True, type="primary"):
                report_spam(gmail_service, gspread_client, msg_id)    
                if st.session_state.action_queue:
                    st.session_state.action_queue = st.session_state.action_queue[1:]
                st.session_state.action_queue.pop(0)
                st.rerun()

            # Action Forms
            if st.session_state.get(f'show_reply_form_{msg_id}', False):
                with st.form(key=f"reply_form_{msg_id}"):
                    st.subheader("Send Reply")
                    reply_text = st.text_area("Your reply:", height=200)
                    if st.form_submit_button("Send"):
                        send_reply(gspread_client, gmail_service, {"message_id": msg_id, "recipient": comm.get('Sender'), "subject": comm.get('Subject/Snippet'), "body": reply_text})
                        st.session_state.action_queue.pop(0)
                        st.rerun()

            if st.session_state.get(f'show_workflow_form_{msg_id}', False):
                with st.form(key=f"workflow_form_{msg_id}"):
                    st.subheader("Start New Workflow")
                    selected_workflow_name = st.selectbox("Workflow Template", options=list(workflow_options.keys()))
                    selected_client = st.text_input("Client", value=comm.get('Sender'))
                    external_deadline = st.date_input("External Deadline (Optional)", value=None)
                    assignee = st.selectbox("Assign To", options=user_list)
                    if st.form_submit_button("Start Workflow"):
                        workflow_details = {'workflow_template_id': workflow_options[selected_workflow_name], 'client': selected_client, 'external_deadline': external_deadline, 'assignee': assignee, 'message_id': msg_id}
                        start_workflow(gspread_client, gmail_service, workflow_details)
                        st.session_state.action_queue.pop(0)
                        st.rerun()

            if st.session_state.get(f'show_task_form_{msg_id}', False):
                with st.form(key=f"task_form_{msg_id}"):
                    st.subheader("Create New Task")
                    task_name = st.text_input("Task Name", value=comm.get('Subject/Snippet', ''))
                    task_client = st.text_input("Client", value=comm.get('Sender'))
                    task_assignee = st.selectbox("Assign To", options=user_list)
                    start_date = st.date_input("Start Date", value=datetime.now())
                    due_date = st.date_input("Due Date", value=datetime.now() + timedelta(days=7))
                    est_time = st.number_input("Est. Time (minutes)", min_value=5, step=5, value=30)
                    enjoyment = st.slider("Enjoyment (1-5)", 1, 5, 3)
                    importance = st.slider("Importance (1-5)", 1, 5, 3)
                    notes = st.text_area("Notes")
                    
                    st.markdown("---")
                    st.subheader("Optional Reply -- Leave Blank to Create a Task but Not Reply to Client")
                    reply_text = st.text_area("Reply to client:", height=150)

                    if st.form_submit_button("Save Task and Reply"):
                        task_details = {
                            "name": task_name, "client": task_client, "assignee": task_assignee,
                            "start_date": start_date, "due_date": due_date, "est_time": est_time,
                            "enjoyment": enjoyment, "importance": importance, "notes": notes,
                            "link": f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
                        }
                        create_task(gspread_client, task_details)
                        
                        # Send reply if text is provided, otherwise just archive
                        if reply_text:
                            send_reply(gspread_client, gmail_service, {
                                "message_id": msg_id, "recipient": comm.get('Sender'),
                                "subject": comm.get('Subject/Snippet'), "body": reply_text
                            })
                        else:
                            archive_message(gmail_service, gspread_client, msg_id)
                        st.session_state.action_queue.pop(0)
                        st.rerun()

        # --- RENDER TASK UI ---
        elif next_item['type'] == 'task':
            task = next_item['data']
            task_id = task['TaskID']
            st.subheader(f"Next Up: Complete Task")
            with st.container(border=True):
                st.write(f"**Task:** {task.get('Task Name')} | **Client:** {task.get('Client', 'N/A')}")
                st.error(f"**Due Date:** {task.get('Due Date')}")
            st.markdown("---")
            st.subheader("Actions")
            
            col1, col2, col3, col4, col5 = st.columns(5)
            if col1.button("Mark as Completed", key=f"complete_{task_id}", use_container_width=True, type="primary"):
                update_task_status(gspread_client, task_id, "Done"); st.session_state.action_queue.pop(0); st.rerun()
            if col2.button("Waiting for Client", key=f"waiting_{task_id}", use_container_width=True):
                set_task_waiting(gspread_client, task_id); st.session_state.action_queue.pop(0); st.rerun()
            if col3.button("Snooze", key=f"snooze_{task_id}", use_container_width=True):
                 st.session_state[f'show_task_snooze_{task_id}'] = not st.session_state.get(f'show_task_snooze_{task_id}', False)
                 st.rerun()
            if col4.button("Reassign", key=f"reassign_{task_id}", use_container_width=True):
                st.session_state[f'show_reassign_{task_id}'] = not st.session_state.get(f'show_reassign_{task_id}', False)
                st.rerun()
            if col5.button("Add Note", key=f"add_note_{task_id}", use_container_width=True):
                st.session_state[f'show_add_note_{task_id}'] = not st.session_state.get(f'show_add_note_{task_id}', False)
                st.rerun()

            # --- ACTION FORMS FOR TASKS ---
            if st.session_state.get(f'show_task_snooze_{task_id}', False):
                st.markdown("##### Snooze Duration")
                s_col1, s_col2, s_col3, s_col4 = st.columns(4)
                
                with s_col1:
                    if st.button("1 Hour", key=f"snooze_1hr_{task_id}", use_container_width=True):
                        snooze_task(gspread_client, task_id, timedelta(hours=1))
                        st.session_state.action_queue.pop(0)
                        st.rerun()
                
                with s_col2:
                    if st.button("Tomorrow", key=f"snooze_day_{task_id}", use_container_width=True):
                        snooze_task(gspread_client, task_id, timedelta(days=1))
                        st.session_state.action_queue.pop(0)
                        st.rerun()

                with s_col3:
                    if st.button("1 Week", key=f"snooze_week_{task_id}", use_container_width=True):
                        snooze_task(gspread_client, task_id, timedelta(weeks=1))
                        st.session_state.action_queue.pop(0)
                        st.rerun()

                with s_col4:
                    if st.button("1 Month", key=f"snooze_month_{task_id}", use_container_width=True):
                        # Approximating 1 month as 30 days
                        snooze_task(gspread_client, task_id, timedelta(days=30))
                        st.session_state.action_queue.pop(0)
                        st.rerun()

            if st.session_state.get(f'show_reassign_{task_id}', False):
                with st.form(f"reassign_form_{task_id}"):
                    st.subheader(f"Reassign Task: {task.get('Task Name')}")
                    new_assignee = st.selectbox("New Assignee", options=user_list)
                    if st.form_submit_button("Save Assignment"):
                        reassign_task(gspread_client, task_id, new_assignee)
                        st.session_state.action_queue.pop(0)
                        st.rerun()

            if st.session_state.get(f'show_add_note_{task_id}', False):
                with st.form(f"note_form_{task_id}"):
                    st.subheader(f"Add Note to: {task.get('Task Name')}")
                    note_text = st.text_area("New Note")
                    if st.form_submit_button("Add Note"):
                        add_note_to_task(gspread_client, task_id, note_text)
                        st.session_state[f'show_add_note_{task_id}'] = False
                        st.rerun()

except Exception as e:
    st.error("An error occurred in the Command Center.")
    st.exception(e)
    if 'action_queue' in st.session_state: del st.session_state['action_queue']

