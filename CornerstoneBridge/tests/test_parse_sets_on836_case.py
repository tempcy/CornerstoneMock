"""ON836 (Commands 6.x) Sets XML uses lowercase tags; CS844 uses PascalCase."""

from __future__ import annotations

import unittest

from cornerstone_bridge.parsers import _parse_sets_response


_GO_SETS_XML = """<?xml version="1.0" encoding="utf-8"?>
<sets ErrorCode="0">
  <FirstIndex>10</FirstIndex>
  <LastIndex>11</LastIndex>
  <TotalSamplesAvailable>100</TotalSamplesAvailable>
  <sets>
    <set Key="00000000000A5AA3">
      <headerFields>
        <field Id="1" RegistryId="SampleType" Label="Type">Sample</field>
        <field Id="2" RegistryId="Set Name" Label="Name">GO-SAMPLE-1</field>
        <field Id="4" RegistryId="Set Analysis Date" Label="Date">2026-08-10</field>
        <field RegistryId="Oxygen Avg." Label="O Avg.">12.3</field>
        <field RegistryId="Nitrogen Avg." Label="N Avg.">45.6</field>
      </headerFields>
    </set>
    <set Key="00000000000A5AA4">
      <headerFields>
        <field Id="2" RegistryId="Set Name">GO-SAMPLE-2</field>
      </headerFields>
    </set>
  </sets>
  <Analytes>
    <Analyte Label="Oxygen">Oxygen</Analyte>
    <Analyte Label="Nitrogen">Nitrogen</Analyte>
  </Analytes>
</sets>
"""


_CS844_SETS_XML = """<?xml version="1.0" encoding="utf-8"?>
<Sets ErrorCode="0">
  <FirstIndex>1</FirstIndex>
  <LastIndex>1</LastIndex>
  <TotalSamplesAvailable>1</TotalSamplesAvailable>
  <Sets>
    <Set Key="ABC">
      <HeaderFields>
        <Field Id="2" RegistryId="Set Name">CS-SAMPLE</Field>
        <Field RegistryId="Carbon Avg.">1.2</Field>
      </HeaderFields>
    </Set>
  </Sets>
  <Analytes>
    <Analyte Label="Carbon">Carbon</Analyte>
  </Analytes>
</Sets>
"""


class ParseSetsOn836Case(unittest.TestCase):
    def test_lowercase_go_sets(self) -> None:
        rows, defs, win = _parse_sets_response(_GO_SETS_XML)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(defs), 2)
        self.assertEqual(win.get("firstIndex"), 10)
        self.assertEqual(win.get("totalSamplesAvailable"), 100)
        self.assertEqual(rows[0]["setKey"], "00000000000A5AA3")
        self.assertEqual(rows[0]["name"], "GO-SAMPLE-1")
        self.assertEqual(rows[0]["fields"].get("Set Name"), "GO-SAMPLE-1")
        o_avg = next(a for a in rows[0]["analyteAvgs"] if a["elementKey"] == "Oxygen")
        self.assertEqual(o_avg["value"], "12.3")

    def test_pascal_cs844_sets_still_works(self) -> None:
        rows, defs, win = _parse_sets_response(_CS844_SETS_XML)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "CS-SAMPLE")
        self.assertEqual(defs[0]["elementKey"], "Carbon")
        self.assertEqual(win.get("lastIndex"), 1)


if __name__ == "__main__":
    unittest.main()
