import os
import unittest
from unittest.mock import patch
from io import StringIO
from contextlib import redirect_stdout
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
spec=spec_from_file_location('check_market_freshness',Path(__file__).parents[1]/'scripts/check_market_freshness.py')
mod=module_from_spec(spec);spec.loader.exec_module(mod)
class ForceTests(unittest.TestCase):
    def test_force_does_not_contact_calendar_or_site(self):
        out=StringIO()
        with patch.dict(os.environ,{'ASTOCK_FORCE_UPDATE':'true'},clear=True),redirect_stdout(out):
            mod.main()
        self.assertIn('update_required=true',out.getvalue())
if __name__=='__main__':unittest.main()
