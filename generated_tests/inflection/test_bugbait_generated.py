"""Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug."""

import inflection


# Catches bug 1: inflection/__init__.py: word = re.sub(r"_id$", "", word) -> word = re.sub(r"_id", "", word)
def test_bug1_humanize_only_strips_trailing_id():
    assert inflection.humanize("my_idol") == "My idol"
    assert inflection.humanize("grid_id_code") == "Grid id code"


# Catches bug 3: inflection/__init__.py: string = re.sub(r'%s{2,}' % re_sep, separator, string) -> string = re.sub(r'%s{3,}' % re_sep, separator, string)
def test_bug3_parameterize_squeezes_exactly_two_dashes():
    assert inflection.parameterize("a--b") == "a-b"


def test_bug3_parameterize_squeezes_two_separators_from_mixed_input():
    assert inflection.parameterize("a -b") == "a-b"


# Catches bug 4: inflection/__init__.py: if re.search(r'(?i)\b(%s)\Z' % inflection, word): -> if re.search(r'(?i)\b(%s)$' % inflection, word):
def test_bug4_singularize_uncountable_with_trailing_newline():
    assert inflection.singularize("jeans") == "jeans"
    assert inflection.singularize("jeans\n") == "jean\n"


# Catches bug 5: inflection/__init__.py: if number % 100 in (11, 12, 13): -> if number % 100 in (11, 12, 13) and number < 1000:
def test_bug5_ordinal_large_teens():
    assert inflection.ordinal(1011) == "th"
    assert inflection.ordinal(1012) == "th"
    assert inflection.ordinal(1013) == "th"
    assert inflection.ordinal(1111) == "th"
    assert inflection.ordinal(1112) == "th"
    assert inflection.ordinal(1113) == "th"


def test_bug5_ordinalize_large_teens():
    assert inflection.ordinalize(1011) == "1011th"
    assert inflection.ordinalize(1012) == "1012th"
    assert inflection.ordinalize(1013) == "1013th"
    assert inflection.ordinalize(1111) == "1111th"


# Catches bug 6: inflection/__init__.py: _irregular('zombie', 'zombies') -> _irregular('zombie', 'zombiez')
def test_bug6_pluralize_zombie():
    assert inflection.pluralize("zombie") == "zombies"


def test_bug6_pluralize_zombie_capitalized():
    assert inflection.pluralize("Zombie") == "Zombies"


# Catches bug 7: inflection/__init__.py: (r"(?i)(s)ynop(sis|ses)$", r"\1ynopsis"), -> (r"(?i)(s)ynop(sis|ses)$", r"\1ynopses"),
def test_bug7_singularize_synopses():
    assert inflection.singularize("synopses") == "synopsis"


def test_bug7_singularize_synopsis_stays_singular():
    assert inflection.singularize("synopsis") == "synopsis"


# Catches bug 8: inflection/__init__.py: return word.replace('_', '-') -> return word.replace('_', '-', 2)
def test_bug8_dasherize_replaces_all_underscores():
    assert inflection.dasherize("a_b_c_d") == "a-b-c-d"


def test_bug8_dasherize_with_many_underscores():
    assert inflection.dasherize("one_two_three_four_five") == "one-two-three-four-five"
