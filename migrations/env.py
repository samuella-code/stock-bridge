from logging.config import fileConfig
from alembic import context
from flask import current_app
config=context.config
if config.config_file_name:fileConfig(config.config_file_name)
db=current_app.extensions["migrate"].db
config.set_main_option("sqlalchemy.url",str(db.engine.url).replace("%","%%"))
def run():
 with db.engine.connect() as connection:
  context.configure(connection=connection,target_metadata=db.metadata,compare_type=True)
  with context.begin_transaction():context.run_migrations()
run()
