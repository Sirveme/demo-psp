"""Tablas de la demo. Cada demostración es una fila: no hay estado de negocio en memoria."""
from sqlalchemy import (Boolean, Column, DateTime, Integer, Numeric, String, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

from app import config

_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, connect_args=_args)
Sesion = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class Demo(Base):
    __tablename__ = "demos"

    id = Column(String(16), primary_key=True)          # aleatorio: referencia_externa = DEMO-{id}
    rubro = Column(String(40), nullable=False)
    tipo = Column(String(10), nullable=False)          # boleta | factura
    doc_tipo = Column(String(2), nullable=False)       # 0 sin doc, 1 DNI, 6 RUC
    doc_numero = Column(String(11), nullable=False)
    cliente_nombre = Column(String(200), nullable=False)
    cliente_direccion = Column(String(250))
    titular = Column(String(120), nullable=False)      # titular del Yape/Plin (pagador)
    monto = Column(Numeric(10, 2), nullable=False)
    billetera = Column(String(20), nullable=False)
    ip = Column(String(64))

    # esperando_pago -> pago_confirmado -> emitiendo -> enviando -> aceptado | rechazado
    # (expirado si no llega el pago en 10 min; error_emision si Facturalo rechaza los datos)
    estado = Column(String(20), nullable=False, default="esperando_pago")
    nivel = Column(String(20))                         # último nivel de PagoOK
    mensaje = Column(String(300))                      # texto simple para la pantalla
    voucher_hora = Column(String(5))                   # HH:MM declarada por la persona

    registrado_en = Column(DateTime, nullable=False)
    pago_en = Column(DateTime)                         # hora del pago según PagoOK
    pago_detectado_en = Column(DateTime)
    emitido_en = Column(DateTime)
    aceptado_en = Column(DateTime)
    segundos = Column(Integer)                         # aceptado_en - pago_en

    consultas_pagook = Column(Integer, default=0)
    pagook_pago_id = Column(Integer)
    pagook_contraparte = Column(String(150))

    facturalo_id = Column(String(64))
    numero_formato = Column(String(30))
    codigo_sunat = Column(String(10))
    mensaje_sunat = Column(String(500))
    leyenda = Column(Text)


class Interesado(Base):
    __tablename__ = "interesados"

    id = Column(Integer, primary_key=True, autoincrement=True)
    demo_id = Column(String(16))
    nombre = Column(String(120), nullable=False)
    celular = Column(String(15), nullable=False)
    ruc = Column(String(11))
    rubro = Column(String(40))
    acepta_contacto = Column(Boolean, nullable=False, default=False)
    creado_en = Column(DateTime, nullable=False)
    ip = Column(String(64))


class CacheConsulta(Base):
    __tablename__ = "cache_consultas"

    clave = Column(String(20), primary_key=True)       # "ruc:20615446565" / "dni:12345678"
    datos = Column(Text, nullable=False)               # JSON
    fuente = Column(String(20))
    creado_en = Column(DateTime, nullable=False)


def crear_tablas():
    Base.metadata.create_all(engine)
