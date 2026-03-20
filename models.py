from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Analysis(db.Model):
    __tablename__ = "analyses"

    id = db.Column(db.Integer, primary_key=True)
    diagnosis_code = db.Column(db.String(20), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    field_code = db.Column(db.String(5))
    area_code = db.Column(db.String(5))
    detailed_area_code = db.Column(db.String(5))
    part_code = db.Column(db.String(5))
    defect_type_code = db.Column(db.String(5))
    defect_code = db.Column(db.String(30))
    material_type = db.Column(db.String(20))
    urgency = db.Column(db.String(10))
    confidence = db.Column(db.Integer)
    risk_percentage = db.Column(db.Integer)
    summary = db.Column(db.Text)
    report_json = db.Column(db.Text)
    construction_method_json = db.Column(db.Text)
    original_image_path = db.Column(db.String(500))
    repaired_image_path = db.Column(db.String(500))
    consultant_notes = db.Column(db.Text, default="")
    counselor_name = db.Column(db.String(50), default="")
    counselor_id = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class ConstructionMethod(db.Model):
    __tablename__ = "construction_methods"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), nullable=False, index=True)
    method_name = db.Column(db.String(100))
    main_use = db.Column(db.Text)
    core_composition = db.Column(db.Text)
    key_advantages = db.Column(db.Text)
    example_link = db.Column(db.String(500))
    deleted_at = db.Column(db.DateTime, nullable=True, default=None)


class Specification(db.Model):
    __tablename__ = "specifications"

    id = db.Column(db.Integer, primary_key=True)
    method_name = db.Column(db.String(100), nullable=False, index=True)
    spec_link = db.Column(db.String(500))
    deleted_at = db.Column(db.DateTime, nullable=True, default=None)


class Counselor(db.Model):
    __tablename__ = "counselors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, default=None)


class AdminAuth(db.Model):
    __tablename__ = "admin_auth"

    id = db.Column(db.Integer, primary_key=True)
    password_hash = db.Column(db.String(255), nullable=False)
    session_timeout_minutes = db.Column(db.Integer, default=240)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
