"""A simulated provider preserving the production intent/send/response boundaries."""
from backend.app import database
from backend.app.services import vocabulary_requests as requests
from backend.app.services.vocabulary_tasks import VocabularyTruncatedError
import httpx


def migrate(db):
    database._run_migrations(db)
    db.execute("UPDATE ai_profiles SET enabled=1,base_url='https://synthetic.invalid',default_model='synthetic'")
    db.commit()


def reply(value=None, operation=None, error=None):
    def invoke(db, *args, **kwargs):
        execution = requests.CURRENT.get()
        if execution:
            execution.before_send(db)
        try:
            result = operation(db, *args, **kwargs) if operation else value
            if isinstance(error, httpx.HTTPError):
                raise error
            if execution:
                execution.responded = True
            if error:
                raise error
            if execution:
                execution.after_response(db)
            return result
        except Exception as cause:
            if execution:
                execution.failure = 'truncated' if isinstance(cause, VocabularyTruncatedError) else 'validation' if execution.responded else 'unknown'
            raise
    return invoke
