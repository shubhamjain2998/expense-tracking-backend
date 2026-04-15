import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Person, TransactionPersonShare
from app.schemas import PersonCreate, PersonOut

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("", response_model=List[PersonOut])
def get_persons(
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    return db.execute(select(Person).where(Person.user_id == user_id)).scalars().all()


@router.post("", response_model=PersonOut, status_code=201)
def create_person(
    body: PersonCreate,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    existing = db.execute(
        select(Person).where(Person.user_id == user_id, Person.name == body.name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409, detail="Person with this name already exists"
        )
    person = Person(user_id=user_id, name=body.name)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


@router.delete("/{id}", status_code=204)
def delete_person(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
):
    person = db.execute(
        select(Person).where(Person.id == id, Person.user_id == user_id)
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    linked = db.execute(
        select(TransactionPersonShare).where(TransactionPersonShare.person_id == id)
    ).first()
    if linked:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete person: linked to one or more processed transactions",
        )

    db.delete(person)
    db.commit()
