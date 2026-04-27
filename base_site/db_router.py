"""Database router: всё ORM-движение идёт в default. БД 'windowgram' —
read-only коннект к базе бота, никаких миграций/записи через ORM.
"""


class WindowgramRouter:
    def db_for_read(self, model, **hints):
        return "default"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "windowgram":
            return False
        return None
