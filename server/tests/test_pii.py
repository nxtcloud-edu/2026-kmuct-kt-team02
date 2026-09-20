import pytest

from server.pii import MASKING_NOTICE, PiiType, mask_pii


@pytest.mark.parametrize(
    ("text", "pii_type", "mask_token"),
    [
        (
            "주민등록번호는 900101-1234567입니다.",
            PiiType.RESIDENT_REGISTRATION_NUMBER,
            "[주민등록번호 마스킹]",
        ),
        (
            "주민등록번호는 9001011234567입니다.",
            PiiType.RESIDENT_REGISTRATION_NUMBER,
            "[주민등록번호 마스킹]",
        ),
        (
            "입금 계좌는 110-123-456789입니다.",
            PiiType.ACCOUNT_NUMBER,
            "[계좌번호 마스킹]",
        ),
        (
            "전화번호는 010-1234-5678입니다.",
            PiiType.PHONE_NUMBER,
            "[전화번호 마스킹]",
        ),
        (
            "이메일은 user.name+demo@example.co.kr입니다.",
            PiiType.EMAIL,
            "[이메일 마스킹]",
        ),
    ],
)
def test_masks_each_supported_pii_type(
    text: str, pii_type: PiiType, mask_token: str
) -> None:
    result = mask_pii(text)

    assert result.detected_types == (pii_type,)
    assert result.notice == MASKING_NOTICE
    assert result.was_masked is True
    assert mask_token in result.masked_text
    assert text != result.masked_text


def test_masks_multiple_pii_values_in_one_sentence() -> None:
    originals = (
        "900101-1234567",
        "110-123-456789",
        "010-9876-5432",
        "privacy@example.com",
    )
    text = (
        f"주민번호 {originals[0]}, 입금 계좌 {originals[1]}, "
        f"전화 {originals[2]}, 이메일 {originals[3]}"
    )

    result = mask_pii(text)

    assert result.detected_types == (
        PiiType.RESIDENT_REGISTRATION_NUMBER,
        PiiType.ACCOUNT_NUMBER,
        PiiType.PHONE_NUMBER,
        PiiType.EMAIL,
    )
    assert all(original not in result.masked_text for original in originals)
    assert result.notice == "민감정보는 입력하지 말아 주세요"


def test_does_not_mask_dates_dday_policy_ids_or_context_free_numbers() -> None:
    text = (
        "기준일은 2026-09-20이고 마감은 D-7입니다. "
        "정책 ID는 SEOUL-2026-001이며 참고번호는 123456789012입니다."
    )

    result = mask_pii(text)

    assert result.masked_text == text
    assert result.detected_types == ()
    assert result.notice is None
    assert result.was_masked is False


def test_account_masking_requires_context_and_ten_or_more_digits() -> None:
    short_with_context = "입금일은 2026-09-20입니다."
    long_without_context = "참고번호는 123456789012입니다."

    assert mask_pii(short_with_context).masked_text == short_with_context
    assert mask_pii(long_without_context).masked_text == long_without_context
