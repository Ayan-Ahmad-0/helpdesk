import os

# Runs before test modules import app.main, so the app picks up the test DB.
os.environ["DATABASE_URL"] = "postgresql://helpdesk:helpdesk@localhost:5432/helpdesk_test"