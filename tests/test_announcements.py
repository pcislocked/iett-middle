from app.routers.routes import fix_encoding


def test_fix_encoding_mojibake():
    # Double encoded text from upstream
    corrupted = "Sefer Â· KayÄ±t Saati"
    fixed = fix_encoding(corrupted)
    assert fixed == "Sefer · Kayıt Saati"


def test_fix_encoding_valid_text():
    # Text that is already valid shouldn't be mangled unless it matches the signature.
    # Actually, the fix_encoding specifically targets mojibake characters.
    # If the text is valid ASCII or simple Turkish without mojibake signs:
    valid = "Normal durak duyurusu: Bekleniyor."
    assert fix_encoding(valid) == valid


def test_fix_encoding_null():
    assert fix_encoding(None) is None
    assert fix_encoding("") == ""
