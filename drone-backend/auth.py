from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt, JWTError

SECRET_KEY = "f3b9c7e8a1d2e3f4a5b6c7d8e9f0a1b2"  # put in .env later
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd.verify(plain, hashed)

def create_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None