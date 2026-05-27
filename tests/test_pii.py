"""Тесты PII-фильтра."""
from __future__ import annotations

from psy_helper.content_gen.pii import detect_pii


def test_detects_phone_ru():
    flags = detect_pii("Звоните +7-999-123-45-67 или 8 (912) 345 67 89")
    phone_flags = [f for f in flags if f.startswith("phone:")]
    assert len(phone_flags) >= 1


def test_detects_email():
    flags = detect_pii("Напишите на test@example.com")
    assert any(f.startswith("email:test@") for f in flags)


def test_detects_common_first_name():
    flags = detect_pii("Маша 38 лет, в супружестве 7 лет")
    assert "name:Маша" in flags


def test_whitelist_anna_oksana_not_flagged():
    """Анна (автор) и Оксана (соавтор) — не PII."""
    flags = detect_pii("Анна и Оксана говорят...")
    assert "name:Анна" not in flags
    assert "name:Оксана" not in flags


def test_custom_allow_names():
    """allow_names расширяет whitelist (например, для исторических фигур)."""
    flags = detect_pii("Сергей пришёл", allow_names={"Сергей"})
    assert "name:Сергей" not in flags


def test_clean_text_no_flags():
    flags = detect_pii("Если Вы устали — это нормально. Это про границы в супружестве.")
    assert flags == []


def test_dedupe_same_name_one_flag():
    """Одно имя несколько раз → один флаг (не спамить ревьюера)."""
    flags = detect_pii("Маша сказала. Маша ушла. Маша вернулась.")
    masha_flags = [f for f in flags if f == "name:Маша"]
    assert len(masha_flags) == 1


def test_year_not_flagged_as_phone():
    """4-значные годы не должны быть phone (нужно ≥10 цифр)."""
    flags = detect_pii("В 2024 году произошло")
    assert not any(f.startswith("phone:") for f in flags)
