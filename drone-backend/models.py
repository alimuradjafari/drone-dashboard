from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class TelemetryLog(Base):
    __tablename__ = "telemetry_log"

    id = Column(Integer, primary_key=True, index=True)
    drone_id = Column(String(50), nullable=False)
    sysid = Column(Integer, nullable=False)
    lat = Column(Float, default=0.0)
    lng = Column(Float, default=0.0)
    alt = Column(Float, default=0.0)
    heading = Column(Float, default=0.0)
    speed = Column(Float, default=0.0)
    battery_pct = Column(Float, default=0.0)
    flight_mode = Column(String(30), default="Unknown")
    armed = Column(Boolean, default=False)
    logged_at = Column(DateTime, default=datetime.utcnow)