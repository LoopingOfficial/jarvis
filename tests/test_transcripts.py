"""Transcripts : normalisation multi-formats et extraction déterministe.

Le test qui compte le plus est `test_rolling_captions_*` : sans lui, un VTT de
sous-titrage en direct multiplie chaque montant par le nombre de cues et le
devis sort faux sans que rien ne le signale.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.transcripts import (  # noqa: E402
    JSON_FMT, SRT, TEXT, VTT,
    _first_json_object, build_quote_payload, detect_format, extract_regex, parse,
)

ROLLING_VTT = """WEBVTT
Kind: captions
Language: fr

NOTE appel du 10 septembre

1
00:00:01.000 --> 00:00:03.500
<v Sophie Renard>Alors pour le logo

2
00:00:03.500 --> 00:00:05.000
<v Sophie Renard>Alors pour le logo il faudrait

3
00:00:05.000 --> 00:00:07.000
<v Sophie Renard>Alors pour le logo il faudrait compter 5 000 EUR HT
"""


class TestFormatDetection(unittest.TestCase):
    def test_by_extension(self):
        self.assertEqual(detect_format("x", "a.vtt"), VTT)
        self.assertEqual(detect_format("x", "a.srt"), SRT)
        self.assertEqual(detect_format("{}", "a.json"), JSON_FMT)
        self.assertEqual(detect_format("x", "a.txt"), TEXT)

    def test_by_content_when_extension_lies(self):
        self.assertEqual(detect_format("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nx", "a.bin"), VTT)
        self.assertEqual(detect_format('{"segments": []}', "a.bin"), JSON_FMT)


class TestRollingCaptions(unittest.TestCase):
    def test_rolling_captions_are_collapsed(self):
        parsed = parse(ROLLING_VTT, "appel.vtt")
        self.assertEqual(parsed["raw_cues"], 3)
        self.assertEqual(parsed["kept_cues"], 1)

    def test_rolling_captions_do_not_multiply_the_amount(self):
        """Le défaut central : sans déduplication, 5 000 € est compté 3 fois."""
        items = extract_regex(parse(ROLLING_VTT, "appel.vtt"))["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["unit_price"], "5000.00")

    def test_exact_duplicates_are_dropped(self):
        payload = json.dumps({"segments": [
            {"speaker": "A", "text": "Refonte 8 000 EUR"},
            {"speaker": "A", "text": "Refonte 8 000 EUR"},
        ]})
        self.assertEqual(len(extract_regex(parse(payload, "a.json"))["items"]), 1)

    def test_sliding_window_keeps_only_the_new_part(self):
        vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nil faudrait compter cinq mille\n\n"
               "00:00:03.000 --> 00:00:05.000\ncompter cinq mille pour le logo complet\n")
        text = parse(vtt, "a.vtt")["text"]
        self.assertEqual(text.count("cinq mille"), 1)
        self.assertIn("logo complet", text)

    def test_different_speakers_are_never_merged(self):
        vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>Bonjour\n\n"
               "00:00:02.000 --> 00:00:03.000\n<v B>Bonjour\n")
        self.assertEqual(len(parse(vtt, "a.vtt")["utterances"]), 2)


class TestCleaning(unittest.TestCase):
    def test_timestamps_headers_and_tags_are_removed(self):
        text = parse(ROLLING_VTT, "appel.vtt")["text"]
        for noise in ("WEBVTT", "-->", "Kind:", "NOTE", "<v "):
            self.assertNotIn(noise, text)

    def test_srt_indexes_are_removed(self):
        srt = "1\n00:00:01,000 --> 00:00:04,000\nSophie: Le pack a 900 EUR\n"
        parsed = parse(srt, "a.srt")
        self.assertEqual(len(parsed["utterances"]), 1)
        self.assertEqual(parsed["utterances"][0].speaker, "Sophie")

    def test_bracket_noise_is_removed(self):
        parsed = parse("A: Le devis [inaudible] a 500 EUR\n", "a.txt")
        self.assertNotIn("inaudible", parsed["text"])

    def test_json_shapes(self):
        for payload in (
            json.dumps([{"speaker": "A", "text": "500 EUR"}]),
            json.dumps({"segments": [{"speaker": "A", "text": "500 EUR"}]}),
            json.dumps({"utterances": [{"from": "A", "content": "500 EUR"}]}),
        ):
            with self.subTest(payload=payload[:40]):
                self.assertEqual(len(parse(payload, "a.json")["utterances"]), 1)

    def test_malformed_json_falls_back_to_text(self):
        parsed = parse('{"segments": [ broken', "a.json")
        self.assertTrue(parsed["utterances"])


class TestRegexExtraction(unittest.TestCase):
    def payload(self, text):
        parsed = parse(text, "a.txt")
        return build_quote_payload(parsed, extract_regex(parsed))

    def test_amount_formats(self):
        for text in ("Le logo a 5 000 EUR", "Le logo a 5000 €", "Le logo a 5 000 euros"):
            with self.subTest(text=text):
                self.assertEqual(self.payload(f"A: {text}\n")["lines"][0]["unit_price"], "5000.00")

    def test_decimals(self):
        self.assertEqual(self.payload("A: Le lot a 1 234,50 EUR\n")["lines"][0]["unit_price"],
                         "1234.50")

    def test_per_unit_keeps_the_unit_price(self):
        line = self.payload("A: Trois declinaisons a 500 EUR l'unite.\n")["lines"][0]
        self.assertEqual(line["quantity"], 3)
        self.assertEqual(line["unit_price"], "500.00")

    def test_total_is_divided_by_the_quantity(self):
        """« 600 € au total » pour 2 journées = 300 € l'unité, pas 600."""
        line = self.payload("A: Deux journees de formation pour 600 EUR au total.\n")["lines"][0]
        self.assertEqual(line["quantity"], 2)
        self.assertEqual(line["unit_price"], "300.00")

    def test_written_quantities(self):
        self.assertEqual(self.payload("A: Trois pages a 100 EUR l'unite.\n")["lines"][0]["quantity"], 3)

    def test_local_vat_does_not_leak_to_other_lines(self):
        """Un taux énoncé sur une ligne ne doit pas contaminer les autres."""
        lines = self.payload(
            "A: Le logo a 5 000 EUR HT.\n"
            "A: Deux journees de formation pour 600 EUR au total, TVA a 10 %.\n")["lines"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["vat_rate"], "20")
        self.assertEqual(lines[1]["vat_rate"], "10")

    def test_isolated_vat_mention_becomes_the_default(self):
        lines = self.payload("A: Tout est a TVA a 10 %.\nA: Le logo a 5 000 EUR.\n")["lines"]
        self.assertEqual(lines[0]["vat_rate"], "10")

    def test_discount_is_captured_once(self):
        self.assertEqual(self.payload("A: Le logo a 900 EUR avec une remise de 15 %.\n")
                         ["discount_pct"], "15")

    def test_contacts_are_captured(self):
        payload = self.payload("A: Mon mail est s.renard@exemple.fr et le 01 98 76 54 32.\n")
        self.assertEqual(payload["emails"], ["s.renard@exemple.fr"])
        self.assertTrue(payload["phones"])

    def test_descriptions_are_clean(self):
        line = self.payload("A: Alors pour le logo il faudrait compter 5 000 EUR HT.\n")["lines"][0]
        self.assertEqual(line["description"], "logo")

    def test_every_item_keeps_its_evidence(self):
        payload = self.payload("A: Trois declinaisons a 500 EUR l'unite.\n")
        self.assertIn("500", payload["items"][0]["evidence"])
        self.assertEqual(payload["items"][0]["source"], "regex")

    def test_no_amount_gives_no_line(self):
        payload = self.payload("A: On verra le budget plus tard.\n")
        self.assertEqual(payload["lines"], [])

    def test_two_amounts_in_one_utterance_are_separated(self):
        lines = self.payload(
            "A: Le logo a 5 000 EUR. La charte a 2 000 EUR.\n")["lines"]
        self.assertEqual(len(lines), 2)
        self.assertEqual({l["unit_price"] for l in lines}, {"5000.00", "2000.00"})


class TestLlmJsonExtraction(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(_first_json_object('{"a": 1}'), {"a": 1})

    def test_object_wrapped_in_prose(self):
        self.assertEqual(_first_json_object('Voici le résultat :\n{"a": 1}\nVoilà.'), {"a": 1})

    def test_nested_braces(self):
        parsed = _first_json_object('{"items": [{"description": "x"}]}')
        self.assertEqual(parsed["items"][0]["description"], "x")

    def test_braces_inside_strings_do_not_break_it(self):
        self.assertEqual(_first_json_object('{"note": "accolade } ici"}')["note"],
                         "accolade } ici")

    def test_no_json_returns_none(self):
        self.assertIsNone(_first_json_object("aucun objet ici"))

    def test_invalid_json_returns_none(self):
        self.assertIsNone(_first_json_object("{pas du json}"))


class TestQuotePayload(unittest.TestCase):
    def test_payload_shape_matches_generate_quote(self):
        parsed = parse("A: Le logo a 5 000 EUR HT.\n", "a.txt")
        payload = build_quote_payload(parsed, extract_regex(parsed))
        for line in payload["lines"]:
            self.assertEqual(set(line), {"description", "quantity", "unit_price", "vat_rate"})

    def test_llm_items_complete_without_duplicating(self):
        parsed = parse("A: Le logo a 5 000 EUR HT.\n", "a.txt")
        regex_result = extract_regex(parsed)
        llm_result = {"ok": True, "client_name": "Sophie Renard", "items": [
            {"description": "logo", "unit_price": "5000.00", "quantity": 1,
             "vat_rate": "20", "source": "llm"},
            {"description": "Hebergement", "unit_price": "300.00", "quantity": 1,
             "vat_rate": "20", "source": "llm"},
        ]}
        payload = build_quote_payload(parsed, regex_result, llm_result)
        self.assertEqual(len(payload["lines"]), 2)
        # La ligne trouvée par les deux passes garde la traçabilité de la regex.
        logo = next(i for i in payload["items"] if i["description"] == "logo")
        self.assertEqual(logo["source"], "regex")
        self.assertEqual(payload["client_name"], "Sophie Renard")

    def test_llm_failure_leaves_the_regex_result_intact(self):
        parsed = parse("A: Le logo a 5 000 EUR HT.\n", "a.txt")
        payload = build_quote_payload(parsed, extract_regex(parsed),
                                      {"ok": False, "error": "hors ligne", "items": []})
        self.assertEqual(len(payload["lines"]), 1)
        self.assertEqual(payload["sources"], {"regex": 1, "llm": 0})


if __name__ == "__main__":
    unittest.main()
