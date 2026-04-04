import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Person, transaction_persons
from app.schemas import PersonCreate, PersonOut

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("", response_model=List[PersonOut])
def get_persons(db: Session = Depends(get_db)):
    rows = db.execute(select(Person)).scalars().all()
    return rows


@router.post("", response_model=PersonOut, status_code=201)
def create_person(body: PersonCreate, db: Session = Depends(get_db)):
    existing = db.execute(
        select(Person).where(Person.name == body.name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409, detail="Person with this name already exists"
        )
    person = Person(name=body.name)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


@router.delete("/{id}", status_code=204)
def delete_person(id: uuid.UUID, db: Session = Depends(get_db)):
    person = db.get(Person, id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    linked = db.execute(
        select(transaction_persons).where(transaction_persons.c.person_id == id)
    ).first()
    if linked:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete person: linked to one or more processed transactions",
        )

    db.delete(person)
    db.commit()
