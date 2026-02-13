## Online Student Complaint Registration and Management System (KIU)

This project implements an online complaint management workflow aligned with the provided functional requirements:

1. User registration and authentication for students, staff, and administrators.
2. Complaint submission with category, description, and optional supporting file.
3. Complaint tracking with timeline and detailed updates.
4. Feedback notifications for complaint progress.
5. Report summary (complaint types, statuses, average response time).
6. Feedback module for student service rating.

### Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: `http://localhost:5000`

### Roles

- **Student:** register/login, submit complaint, track progress, submit feedback.
- **Staff/Admin:** view all complaints, post updates, view reports.
