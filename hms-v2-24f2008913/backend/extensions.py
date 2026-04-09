from flask_caching import Cache
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from flask_migrate import Migrate


jwt = JWTManager()
cache = Cache()
mail = Mail()
migrate = Migrate()
