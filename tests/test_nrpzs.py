from pathlib import Path

import pytest

from radar import nrpzs

FIX = Path(__file__).parent / "fixtures"


def load(name, **overrides):
    reader, fh = nrpzs.open_csv(FIX / name)
    with fh:
        cols = nrpzs.resolve_columns(reader.fieldnames, overrides)
        return {p.place_id: p for p in nrpzs.iter_providers(reader, cols)}


def test_old_format_filters_to_tracked_specialties():
    provs = load("old_format.csv")
    assert set(provs) == {"1001", "1002", "1004", "1005"}  # hospital 1003 dropped
    assert provs["1002"].specialties == {"praktik"}


def test_specialists_only_with_outpatient_care():
    assert nrpzs.parse_specialties("Oftalmologie, neurologie", "ambulantní péče") == {"ocni", "neurolog"}
    assert nrpzs.parse_specialties("neurologie", "akutní lůžková péče standardní") == set()
    assert nrpzs.parse_specialties("zubní lékařství, psychiatrie", "lůžková péče") == {"zubar"}
    assert nrpzs.parse_specialties("Otorinolaryngologie a chirurgie hlavy a krku") == {"orl"}
    assert nrpzs.parse_specialties("korektivní dermatologie", "ambulantní péče") == set()


def test_duplicate_place_rows_are_merged():
    p = load("old_format.csv")["1001"]
    assert p.specialties == {"zubar", "hygienistka"}
    assert p.raw_specialties == "zubní lékařství, ortodoncie, dentální hygiena"


def test_fields_and_slugs():
    provs = load("old_format.csv")
    p = provs["1001"]
    assert (p.city, p.city_slug, p.postcode, p.ico) == ("Brno", "brno", "60200", "11111111")
    assert (p.lat, p.lng) == (49.1951, 16.6068)
    assert provs["1005"].city_slug == "praha-6"
    assert provs["1005"].specialties == {"zubar"}  # label matching is case-insensitive


def test_swapped_and_missing_coordinates():
    provs = load("old_format.csv")
    assert (provs["1004"].lat, provs["1004"].lng) == (50.66, 14.032)
    assert provs["1004"].city_slug == "usti-nad-labem"
    assert (provs["1005"].lat, provs["1005"].lng) == (None, None)


def test_new_format_semicolon_cp1250_combined_gps():
    provs = load("new_format_cp1250.csv")
    assert len(provs) == 2  # pharmacy dropped
    by_name = {p.name: p for p in provs.values()}
    p = by_name["Stomatologie Smyšlená"]
    assert p.specialties == {"zubar"}
    assert (p.lat, p.lng) == (49.7438, 13.3736)
    assert p.facility_id == "A1"
    assert p.place_id.startswith("A1|Plzeň")  # no place-id column -> synthetic key


def test_map_override_and_errors():
    reader, fh = nrpzs.open_csv(FIX / "old_format.csv")
    with fh:
        cols = nrpzs.resolve_columns(reader.fieldnames, {"name": "DruhZarizeni"})
        assert cols["name"] == "DruhZarizeni"
        with pytest.raises(ValueError, match="no such column"):
            nrpzs.resolve_columns(reader.fieldnames, {"name": "Nope"})
    with pytest.raises(ValueError, match="could not find"):
        nrpzs.resolve_columns(["foo", "bar"])


@pytest.mark.parametrize("gps,expected", [
    ("50.08 14.42", (50.08, 14.42)),
    ("50.08, 14.42", (50.08, 14.42)),
    ("50,08 14,42", (50.08, 14.42)),
    ("0 0", (None, None)),
    ("garbage", (None, None)),
])
def test_gps_parsing(gps, expected):
    assert nrpzs.parse_coords(None, None, gps) == expected


def test_same_named_towns_get_separate_slugs():
    mk = lambda pid, district: nrpzs.Provider(pid, "x", {"zubar"}, "", city="Benešov", city_slug="benesov", district=district)
    provs = [mk("1", "Benešov"), mk("2", "Benešov"), mk("3", "Blansko"), mk("4", None)]
    nrpzs.disambiguate_slugs(provs)
    assert [p.city_slug for p in provs] == ["benesov", "benesov", "benesov-blansko", "benesov"]


@pytest.mark.parametrize("facility,raw,expected", [
    ("Koroner", "všeobecné praktické lékařství", set()),
    ("Zdravotnická zachranná služba", "všeobecné praktické lékařství", set()),
    ("Samostatná stomatologická laboratoř", "zubní lékařství", set()),
    ("Lázeňská léčebna", "neurologie", set()),
    ("Zařízení závodní preventivní péče", "všeobecné praktické lékařství", set()),
    ("Psychiatrická nemocnice", "psychiatrie, dermatovenerologie", {"psychiatr"}),
    ("Nemocnice", "urologie", {"urolog"}),                       # hospital outpatient clinic: stays
    ("Samostatná ordinace lékaře specialisty", "chirurgie", {"chirurg"}),
])
def test_closed_facilities_are_left_out(facility, raw, expected):
    assert nrpzs.parse_specialties(raw, "ambulantní péče", facility) == expected


def test_care_home_and_company_doctors_are_left_out():
    cols = {"name": "n", "specialties": "s", "place_id": "id", "city": "c"}
    rows = [{"n": "Domov pro seniory Chodov", "s": "všeobecné praktické lékařství", "id": "1", "c": "Praha"},
            {"n": "ČESKÁ NÁRODNÍ BANKA, závodní ordinace", "s": "všeobecné praktické lékařství", "id": "2", "c": "Praha"},
            {"n": "MUDr. Poupová, praktický a závodní lékař, s.r.o.", "s": "všeobecné praktické lékařství", "id": "3", "c": "Plzeň"}]
    assert [p.place_id for p in nrpzs.iter_providers(rows, cols)] == ["3"]



@pytest.mark.parametrize("facility,raw,expected", [
    ("Samostatná ordinace PL - gynekologa", "gynekologie a porodnictví, chirurgie", {"gynekolog"}),
    ("Samost. ordinace všeob. prakt. lékaře", "všeobecné praktické lékařství, neurologie", {"praktik"}),
    ("Samostatná ordinace PL - stomatologa", "zubní lékařství, dentální hygiena", {"zubar", "hygienistka"}),
    ("Poskytovatel amb. služeb (do 5 oborů)", "gynekologie a porodnictví, ortopedie a traumatologie pohybového ústrojí", {"gynekolog", "ortoped"}),
])
def test_single_kind_practices(facility, raw, expected):
    assert nrpzs.parse_specialties(raw, "ambulantní péče", facility) == expected
