import os,json
from pathlib import Path
PROJECT_FILE=os.getenv('PROJECT_FILE')
if not PROJECT_FILE:
    default=Path('/app/project.json')
    PROJECT_FILE=str(default if default.exists() else Path(__file__).resolve().parents[2]/'project.json')
CONFIG=json.loads(Path(PROJECT_FILE).read_text())
DATABASE_URL=os.environ['DATABASE_URL']
REDIS_URL=os.getenv('REDIS_URL','redis://redis:6379/0')
SECRET=os.environ['JWT_SECRET']
