# -*- coding: utf-8 -*-
"""
    test_transaction.py
    :copyright: (C) 2014-2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
import os
import unittest
import datetime
import random
from stripe.test.helper import DUMMY_CARD
from dateutil.relativedelta import relativedelta

from trytond.tests.test_tryton import DB_NAME, USER, CONTEXT, POOL
import trytond.tests.test_tryton
from trytond.transaction import Transaction
from trytond.exceptions import UserError

DUMMY_CARD['exp_month'] = '0%d' % DUMMY_CARD['exp_month']
DUMMY_CARD['exp_year'] = str(DUMMY_CARD['exp_year'])


class TestTransaction(unittest.TestCase):
    """
    Test transaction
    """

    def setUp(self):
        """
        Set up data used in the tests.
        """
        trytond.tests.test_tryton.install_module('payment_gateway_stripe')

        self.Currency = POOL.get('currency.currency')
        self.Company = POOL.get('company.company')
        self.Party = POOL.get('party.party')
        self.User = POOL.get('res.user')
        self.Journal = POOL.get('account.journal')
        self.PaymentGateway = POOL.get('payment_gateway.gateway')
        self.PaymentTransaction = POOL.get('payment_gateway.transaction')
        self.AccountMove = POOL.get('account.move')
        self.PaymentProfile = POOL.get('party.payment_profile')
        self.UseCardView = POOL.get('payment_gateway.transaction.use_card.view')

        assert 'STRIPE_API_KEY' in os.environ, \
            "STRIPE_API_KEY not given. Hint:Use export STRIPE_API_KEY=<number>"

    def _create_fiscal_year(self, date=None, company=None):
        """
        Creates a fiscal year and requried sequences
        """
        FiscalYear = POOL.get('account.fiscalyear')
        Sequence = POOL.get('ir.sequence')
        Company = POOL.get('company.company')

        if date is None:
            date = datetime.date.today()

        if company is None:
            company, = Company.search([], limit=1)

        fiscal_year, = FiscalYear.create([{
            'name': '%s' % date.year,
            'start_date': date + relativedelta(month=1, day=1),
            'end_date': date + relativedelta(month=12, day=31),
            'company': company,
            'post_move_sequence': Sequence.create([{
                'name': '%s' % date.year,
                'code': 'account.move',
                'company': company,
            }])[0],
        }])
        FiscalYear.create_period([fiscal_year])
        return fiscal_year

    def _create_coa_minimal(self, company):
        """Create a minimal chart of accounts
        """
        AccountTemplate = POOL.get('account.account.template')
        Account = POOL.get('account.account')

        account_create_chart = POOL.get(
            'account.create_chart', type="wizard")

        account_template, = AccountTemplate.search(
            [('parent', '=', None)]
        )

        session_id, _, _ = account_create_chart.create()
        create_chart = account_create_chart(session_id)
        create_chart.account.account_template = account_template
        create_chart.account.company = company
        create_chart.transition_create_account()

        receivable, = Account.search([
            ('kind', '=', 'receivable'),
            ('company', '=', company),
        ])
        payable, = Account.search([
            ('kind', '=', 'payable'),
            ('company', '=', company),
        ])
        create_chart.properties.company = company
        create_chart.properties.account_receivable = receivable
        create_chart.properties.account_payable = payable
        create_chart.transition_create_properties()

    def _get_account_by_kind(self, kind, company=None, silent=True):
        """Returns an account with given spec
        :param kind: receivable/payable/expense/revenue
        :param silent: dont raise error if account is not found
        """
        Account = POOL.get('account.account')
        Company = POOL.get('company.company')

        if company is None:
            company, = Company.search([], limit=1)

        accounts = Account.search([
            ('kind', '=', kind),
            ('company', '=', company)
        ], limit=1)
        if not accounts and not silent:
            raise Exception("Account not found")
        if not accounts:
            return None
        account, = accounts
        return account

    def create_payment_profile(self, party, gateway):
        """
        Create a payment profile for Stripe payment gateway
        """
        ProfileWizard = POOL.get(
            'party.party.payment_profile.add', type="wizard"
        )

        profile_wizard = ProfileWizard(
            ProfileWizard.create()[0]
        )
        profile_wizard.card_info.owner = party.name
        profile_wizard.card_info.number = DUMMY_CARD['number']
        profile_wizard.card_info.expiry_month = DUMMY_CARD['exp_month']
        profile_wizard.card_info.expiry_year = DUMMY_CARD['exp_year']
        profile_wizard.card_info.csc = str(random.randint(556, 999))
        profile_wizard.card_info.gateway = gateway
        profile_wizard.card_info.provider = gateway.provider
        profile_wizard.card_info.address = party.addresses[0]
        profile_wizard.card_info.party = party

        with Transaction().set_context(return_profile=True):
            profile = profile_wizard.transition_add()
        return profile

    def setup_defaults(self):
        """
        Creates default data for testing
        """
        currency, = self.Currency.create([{
            'name': 'US Dollar',
            'code': 'USD',
            'symbol': '$',
        }])

        with Transaction().set_context(company=None):
            company_party, = self.Party.create([{
                'name': 'Openlabs'
            }])

        self.company, = self.Company.create([{
            'party': company_party,
            'currency': currency,
        }])

        self.User.write([self.User(USER)], {
            'company': self.company,
            'main_company': self.company,
        })

        CONTEXT.update(self.User.get_preferences(context_only=True))

        # Create Fiscal Year
        self._create_fiscal_year(company=self.company.id)
        # Create Chart of Accounts
        self._create_coa_minimal(company=self.company.id)
        # Create Cash journal
        self.cash_journal, = self.Journal.search(
            [('type', '=', 'cash')], limit=1
        )
        self.Journal.write([self.cash_journal], {
            'debit_account': self._get_account_by_kind('expense').id
        })

        self.stripe_gateway = self.PaymentGateway(
            name='Credit Card - Stripe',
            journal=self.cash_journal,
            provider='stripe',
            method='credit_card',
            stripe_api_key_test=os.environ['STRIPE_API_KEY'],
            stripe_api_key_live='somekey',
            test=True
        )
        self.stripe_gateway.save()

        # Create parties
        self.party1, = self.Party.create([{
            'name': 'Test party - 1',
            'addresses': [('create', [{
                'name': 'Test Party %s' % random.randint(1, 999),
                'street': 'Test Street %s' % random.randint(1, 999),
                'city': 'Test City %s' % random.randint(1, 999),
            }])],
            'account_receivable': self._get_account_by_kind(
                'receivable').id,
        }])
        self.party2, = self.Party.create([{
            'name': 'Test party - 2',
            'addresses': [('create', [{
                'name': 'Test Party',
                'street': 'Test Street',
                'city': 'Test City',
            }])],
            'account_receivable': self._get_account_by_kind(
                'receivable').id,
        }])
        self.party3, = self.Party.create([{
            'name': 'Test party - 3',
            'addresses': [('create', [{
                'name': 'Test Party',
                'street': 'Test Street',
                'city': 'Test City',
            }])],
            'account_receivable': self._get_account_by_kind(
                'receivable').id,
        }])

        self.card_data = self.UseCardView(
            number=DUMMY_CARD['number'],
            expiry_month=DUMMY_CARD['exp_month'],
            expiry_year=DUMMY_CARD['exp_year'],
            csc=str(random.randint(100, 555)),
            owner='Test User -1',
        )

    def test_0010_test_add_payment_profile(self):
        """
        Test adding payment profile to a Party
        """
        with Transaction().start(DB_NAME, USER, context=CONTEXT):
            self.setup_defaults()

            profile = self.create_payment_profile(
                self.party2, self.stripe_gateway
            )

            self.assertEqual(profile.party.id, self.party2.id)
            self.assertEqual(profile.gateway, self.stripe_gateway)
            self.assertEqual(
                profile.last_4_digits, DUMMY_CARD['number'][-4:]
            )
            self.assertEqual(
                profile.expiry_month, DUMMY_CARD['exp_month']
            )
            self.assertEqual(
                profile.expiry_year, DUMMY_CARD['exp_year']
            )
            self.assertIsNotNone(profile.stripe_customer_id)

    def test_0020_test_transaction_capture(self):
        """
        Test capture transaction
        """
        with Transaction().start(DB_NAME, USER, context=CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({'company': self.company.id}):
                # Case I: Payment Profile
                payment_profile = self.create_payment_profile(
                    self.party1, self.stripe_gateway
                )
                transaction1, = self.PaymentTransaction.create([{
                    'party': self.party1.id,
                    'credit_account': self.party1.account_receivable.id,
                    'address': self.party1.addresses[0].id,
                    'payment_profile': payment_profile.id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction1)
                self.assertEqual(transaction1.state, 'draft')

                # Capture transaction
                self.PaymentTransaction.capture([transaction1])
                self.assertEqual(transaction1.state, 'posted')

                # Case II: No Payment Profile
                transaction2, = self.PaymentTransaction.create([{
                    'party': self.party2.id,
                    'credit_account': self.party2.account_receivable.id,
                    'address': self.party2.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction2)
                self.assertEqual(transaction2.state, 'draft')

                # Capture transaction
                transaction2.capture_stripe(card_info=self.card_data)
                self.assertEqual(transaction2.state, 'posted')

                # Case III: Transaction Failure on invalid amount
                transaction3, = self.PaymentTransaction.create([{
                    'party': self.party1.id,
                    'credit_account': self.party1.account_receivable.id,
                    'address': self.party1.addresses[0].id,
                    'payment_profile': payment_profile.id,
                    'gateway': self.stripe_gateway.id,
                    'amount': -1,
                }])
                self.assert_(transaction3)
                self.assertEqual(transaction3.state, 'draft')

                # Capture transaction
                self.PaymentTransaction.capture([transaction3])
                self.assertEqual(transaction3.state, 'failed')

                # Case IV: Assert error when new customer is there with
                # no payment profile and card info
                transaction4, = self.PaymentTransaction.create([{
                    'party': self.party3.id,
                    'credit_account': self.party3.account_receivable.id,
                    'address': self.party3.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction4)
                self.assertEqual(transaction4.state, 'draft')

                # Capture transaction
                with self.assertRaises(UserError):
                    self.PaymentTransaction.capture([transaction4])

    def test_0030_test_transaction_auth_only(self):
        """
        Test auth_only transaction
        """
        with Transaction().start(DB_NAME, USER, context=CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({'company': self.company.id}):
                # Case I: Payment Profile
                payment_profile = self.create_payment_profile(
                    self.party1, self.stripe_gateway
                )
                transaction1, = self.PaymentTransaction.create([{
                    'party': self.party1.id,
                    'credit_account': self.party1.account_receivable.id,
                    'address': self.party1.addresses[0].id,
                    'payment_profile': payment_profile.id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction1)
                self.assertEqual(transaction1.state, 'draft')

                # Authorize transaction
                self.PaymentTransaction.authorize([transaction1])
                self.assertEqual(transaction1.state, 'authorized')

                # Case II: No Payment Profile
                transaction2, = self.PaymentTransaction.create([{
                    'party': self.party2.id,
                    'credit_account': self.party2.account_receivable.id,
                    'address': self.party2.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction2)
                self.assertEqual(transaction2.state, 'draft')

                # Authorize transaction
                transaction2.authorize_stripe(card_info=self.card_data)
                self.assertEqual(transaction2.state, 'authorized')

                # Case III: Transaction Failure on invalid amount
                transaction3, = self.PaymentTransaction.create([{
                    'party': self.party1.id,
                    'credit_account': self.party1.account_receivable.id,
                    'address': self.party1.addresses[0].id,
                    'payment_profile': payment_profile.id,
                    'gateway': self.stripe_gateway.id,
                    'amount': -1,
                }])
                self.assert_(transaction3)
                self.assertEqual(transaction3.state, 'draft')

                # Authorize transaction
                self.PaymentTransaction.authorize([transaction3])
                self.assertEqual(transaction3.state, 'failed')

                # Case IV: Assert error when new customer is there with
                # no payment profile and card info
                transaction3, = self.PaymentTransaction.create([{
                    'party': self.party3.id,
                    'credit_account': self.party3.account_receivable.id,
                    'address': self.party3.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction3)
                self.assertEqual(transaction3.state, 'draft')

                # Authorize transaction
                with self.assertRaises(UserError):
                    self.PaymentTransaction.authorize([transaction3])

    def test_0040_test_transaction_auth_and_settle(self):
        """
        Test auth_and_settle transaction
        """
        with Transaction().start(DB_NAME, USER, context=CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({'company': self.company.id}):
                # Case I: Same or less amount than authorized amount
                transaction1, = self.PaymentTransaction.create([{
                    'party': self.party3.id,
                    'credit_account': self.party3.account_receivable.id,
                    'address': self.party3.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction1)
                self.assertEqual(transaction1.state, 'draft')

                # Authorize transaction
                transaction1.authorize_stripe(card_info=self.card_data)
                self.assertEqual(transaction1.state, 'authorized')

                # Assert if transaction succeeds
                self.PaymentTransaction.settle([transaction1])
                self.assertEqual(transaction1.state, 'posted')

                # Case II: More amount than authorized amount
                transaction2, = self.PaymentTransaction.create([{
                    'party': self.party3.id,
                    'credit_account': self.party3.account_receivable.id,
                    'address': self.party3.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                }])
                self.assert_(transaction2)
                self.assertEqual(transaction2.state, 'draft')

                # Authorize transaction
                transaction2.authorize_stripe(card_info=self.card_data)
                self.assertEqual(transaction2.state, 'authorized')

                # Assert if transaction fails.
                self.PaymentTransaction.write([transaction2], {
                    'amount': 6,
                })
                self.PaymentTransaction.settle([transaction2])
                self.assertEqual(transaction2.state, 'failed')

    def test_0050_test_transaction_auth_and_cancel(self):
        """
        Test auth_and_void transaction
        """
        with Transaction().start(DB_NAME, USER, context=CONTEXT):
            self.setup_defaults()

            with Transaction().set_context({'company': self.company.id}):
                transaction1, = self.PaymentTransaction.create([{
                    'party': self.party2.id,
                    'credit_account': self.party2.account_receivable.id,
                    'address': self.party2.addresses[0].id,
                    'gateway': self.stripe_gateway.id,
                    'amount': random.randint(1, 5),
                    'state': 'in-progress',
                }])
                self.assert_(transaction1)
                self.assertEqual(transaction1.state, 'in-progress')

                # Assert User error if cancel request is sent
                # in state other than authorized
                with self.assertRaises(UserError):
                    self.PaymentTransaction.cancel([transaction1])

                transaction1.state = 'draft'
                transaction1.save()

                # Authorize transaction
                transaction1.authorize_stripe(card_info=self.card_data)
                self.assertEqual(transaction1.state, 'authorized')

                # Settle transaction
                self.PaymentTransaction.cancel([transaction1])
                self.assertEqual(transaction1.state, 'cancel')


def suite():
    "Define suite"
    test_suite = trytond.tests.test_tryton.suite()
    test_suite.addTests(
        unittest.TestLoader().loadTestsFromTestCase(TestTransaction)
    )
    return test_suite


if __name__ == '__main__':
    unittest.TextTestRunner(verbosity=2).run(suite())
