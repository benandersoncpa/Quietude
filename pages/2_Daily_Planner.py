import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plan_my_day as planner
# --- FIX: Correctly import from 'quietude.py' ---
from quietude import (
    update_task_status, set_task_waiting, snooze_task, 
    reassign_task, add_note_to_task, 
    fetch_sheet_data, authenticate_google,
    run_fetch_communications
)

st.set_page_config(layout="wide")
st.title("🗓️ Daily Planner")
st.link_button("🚀 Open Quietude OS Google Sheet", "https://docs.google.com/spreadsheets/d/1o5LmRv4MUQmO84bouTiBdqFzZu-lqx8V_YDVSBSsi2c/edit?gid=0#gid=0")

gspread_client, gmail_service, sheets_service = authenticate_google()
users_df = fetch_sheet_data(gspread_client, "Users")
# --- FIX: Correctly access the 'Users' column ---
user_list = users_df['Users'].tolist() if not users_df.empty and 'Users' in users_df.columns else []

# Initialize session state variables if they don't exist
if "daily_schedule" not in st.session_state:
    st.session_state.daily_schedule = None
if "fixed_events" not in st.session_state:
    st.session_state.fixed_events = None
if 'last_comms_fetch' not in st.session_state:
    st.session_state.last_comms_fetch = None


if st.button("✨ Refresh Plan"):
    with st.spinner("Analyzing your tasks and calendar..."):
        schedule_exists = st.session_state.daily_schedule is not None
        schedule, events = planner.generate_schedule(schedule_already_generated=schedule_exists)
        st.session_state.daily_schedule = schedule
        st.session_state.fixed_events = events

st.markdown("---")

if st.session_state.daily_schedule is not None:
    st.header(f"Your Plan for {datetime.now().strftime('%A, %B %d')}")
    full_schedule = []
    
    if st.session_state.fixed_events:
        for event in st.session_state.fixed_events:
            start = planner.date_parser.parse(event['start'].get('dateTime', event['start'].get('date'))).astimezone(datetime.now().astimezone().tzinfo)
            end = planner.date_parser.parse(event['end'].get('dateTime', event['end'].get('date'))).astimezone(datetime.now().astimezone().tzinfo)
            full_schedule.append({'title': event['summary'], 'start': start, 'end': end, 'type': 'appointment'})

    if st.session_state.daily_schedule:
        full_schedule.extend(st.session_state.daily_schedule)

    full_schedule = sorted(full_schedule, key=lambda x: x['start'])
    now = datetime.now().astimezone()

    for item in full_schedule:
        start_time = item['start'].strftime('%I:%M %p')
        end_time = item['end'].strftime('%I:%M %p')
        
        if item.get('type') == 'task':
            expander_title = f"**{start_time} - {end_time}** | ✅ {item['title']} ({item.get('client', 'N/A')})"
            with st.expander(expander_title):
                main_col, action_col = st.columns([4, 1])
                with main_col:
                    st.write(f"**Client:** {item.get('client', 'N/A')}")
                with action_col:
                    st.write("**Actions**")
                    task_id = item['task_id']
                    if st.button("Mark as Completed", key=f"complete_{task_id}", use_container_width=True, type="primary"):
                        # 1. Update the status
                        update_task_status(gspread_client, task_id, "Done")
                        # 2. Regenerate the entire plan to include the new task
                        with st.spinner("Updating plan..."):
                            schedule, events = planner.generate_schedule(schedule_already_generated=True)
                            st.session_state.daily_schedule = schedule
                            st.session_state.fixed_events = events
                        # 3. Rerun the app to display the updated plan
                        st.rerun()
                    if st.button("Waiting for Client", key=f"waiting_{task_id}", use_container_width=True):
                        set_task_waiting(gspread_client, task_id); st.rerun()
                    if st.button("Snooze", key=f"snooze_{task_id}", use_container_width=True):
                        st.session_state[f'show_task_snooze_{task_id}'] = True
                        st.cache_data.clear() # Add this line BEFORE the rerun
                        st.rerun()
                    if st.button("Reassign", key=f"reassign_{task_id}", use_container_width=True):
                        st.session_state[f'show_reassign_{task_id}'] = True
                        st.cache_data.clear() # Add this line BEFORE the rerun
                        st.rerun()
                    if st.button("Add Note", key=f"add_note_{task_id}", use_container_width=True):
                        st.session_state[f'show_add_note_{task_id}'] = True
                        st.cache_data.clear() # Add this line BEFORE the rerun
                        st.rerun()

                    if st.session_state.get(f'show_task_snooze_{task_id}', False):
                        if st.button("Tomorrow", key=f"snooze_day_{task_id}", use_container_width=True):
                            snooze_task(gspread_client, task_id, timedelta(days=1)); st.rerun()
                    
                    if st.session_state.get(f'show_reassign_{task_id}', False):
                        with st.form(f"reassign_form_{task_id}"):
                            new_assignee = st.selectbox("New Assignee", options=user_list, key=f"assignee_{task_id}")
                            if st.form_submit_button("Save"):
                                reassign_task(gspread_client, task_id, new_assignee); st.rerun()

                    if st.session_state.get(f'show_add_note_{task_id}', False):
                        with st.form(f"note_form_{task_id}"):
                            note_text = st.text_area("New Note", key=f"note_{task_id}")
                            if st.form_submit_button("Add"):
                                add_note_to_task(gspread_client, task_id, note_text); st.rerun()
        else:
            col1, col2 = st.columns([1, 4])
            with col1:
                st.write(f"**{start_time} - {end_time}**")
            with col2:
                if item.get('type') == 'appointment':
                    st.info(f"🗓️ {item['title']} (Existing Appointment)")
                
                elif item.get('type') == 'comms':
                    is_active = item['start'] <= now <= item['end']
                    if is_active:
                        st.warning(f"💬 **ACTIVE:** {item['title']}")
                        
                        if (st.session_state.last_comms_fetch is None or 
                            now - st.session_state.last_comms_fetch > timedelta(minutes=10)):
                            with st.spinner("Automatically fetching communications..."):
                                run_fetch_communications(gmail_service, gspread_client)
                                st.session_state.last_comms_fetch = now
                                st.cache_data.clear() # Add this line BEFORE the rerun
                                st.cache_data.clear() # Add this line BEFORE the rerun
                                st.rerun()

                        if st.button("🔄 Fetch New Communications Now", key="fetch_now"):
                            with st.spinner("Fetching communications..."):
                                run_fetch_communications(gmail_service, gspread_client)
                                st.session_state.last_comms_fetch = now
                                st.cache_data.clear() # Add this line BEFORE the rerun
                                st.cache_data.clear() # Add this line BEFORE the rerun
                                st.rerun()
                    else:
                        st.warning(f"💬 {item['title']}")
                
                elif item.get('type') == 'break':
                    st.error(f"☕️ {item['title']}")
                elif item.get('type') == 'focus':
                     st.success(f"🎯 {item['title']}")
                else:
                    st.write(item.get('title', ''))
else:
    st.info("Click the button above to generate your daily plan.")