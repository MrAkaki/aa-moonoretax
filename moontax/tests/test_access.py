"""Hierarchical permission helpers: admin ⊃ staff ⊃ basic (Requirements §9)."""

from django.test import SimpleTestCase

from moontax import access


class FakeUser:
    def __init__(self, *perms):
        self._perms = set(perms)

    def has_perm(self, perm):
        return perm in self._perms


class HierarchyTest(SimpleTestCase):
    def test_basic_only(self):
        u = FakeUser(access.BASIC)
        self.assertTrue(access.can_basic(u))
        self.assertFalse(access.can_staff(u))
        self.assertFalse(access.can_admin(u))
        self.assertEqual(access.access_level(u), "basic")

    def test_staff_implies_basic(self):
        u = FakeUser(access.STAFF)
        self.assertTrue(access.can_basic(u))
        self.assertTrue(access.can_staff(u))
        self.assertFalse(access.can_admin(u))
        self.assertEqual(access.access_level(u), "staff")

    def test_admin_implies_all(self):
        u = FakeUser(access.ADMIN)
        self.assertTrue(access.can_basic(u))
        self.assertTrue(access.can_staff(u))
        self.assertTrue(access.can_admin(u))
        self.assertEqual(access.access_level(u), "admin")

    def test_none(self):
        u = FakeUser()
        self.assertFalse(access.can_basic(u))
        self.assertIsNone(access.access_level(u))
