from typing import List, Optional, Dict
from sqlalchemy import BigInteger, ForeignKey, String, JSON, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    
    groups: Mapped[List["AddressGroup"]] = relationship(back_populates="user", cascade="all, delete-orphan")

class Admin(Base):
    __tablename__ = "admins"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

class AddressGroup(Base):
    __tablename__ = "address_groups"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    user: Mapped["User"] = relationship(back_populates="groups")
    addresses: Mapped[List["Address"]] = relationship(back_populates="group", cascade="all, delete-orphan")

class Address(Base):
    __tablename__ = "addresses"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("address_groups.id"), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmations_target: Mapped[int] = mapped_column(Integer, default=1)
    notify_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    
    group: Mapped["AddressGroup"] = relationship(back_populates="addresses")

class Transaction(Base):
    __tablename__ = "transactions"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    txid: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # notified_confs: Dict[str, List[str]] -> { "telegram_id": ["0", "1", "target"] }
    notified_confs: Mapped[Dict] = mapped_column(JSON, default=dict)
