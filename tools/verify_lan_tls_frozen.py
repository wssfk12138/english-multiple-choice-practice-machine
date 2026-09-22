"""Isolated frozen-runtime smoke test; never opens the user's database or UI."""
import unittest
from tests.test_lan_tls_host import ProductionTlsHostTests
from tests.test_lan_discovery import DiscoveryTests

if __name__ == '__main__':
    suite = unittest.TestSuite()
    for case in (ProductionTlsHostTests, DiscoveryTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
