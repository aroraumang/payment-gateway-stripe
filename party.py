# -*- coding: utf-8 -*-
"""
    party.py

    :copyright: (c) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
from trytond.pool import PoolMeta, Pool
from trytond.model import fields

__metaclass__ = PoolMeta
__all__ = ['Address', 'PaymentProfile', 'Party']


class Address:
    __name__ = 'party.address'

    def get_address_for_stripe(self):
        """
        Return the address as a dictionary for stripe
        """
        return {
            'address_line1': self.street,
            'address_line2': self.streetbis,
            'address_city': self.city,
            'address_zip': self.zip,
            'address_state': self.subdivision and self.subdivision.name,
            'address_country': self.country and self.country.name,
        }


class PaymentProfile:
    __name__ = 'party.payment_profile'

    stripe_customer_id = fields.Char(
        'Stripe Customer ID', readonly=True
    )


class Party:
    __name__ = 'party.party'

    def _get_stripe_customer_id(self, gateway_id):
        """
        Extracts and returns customer id from party's payment profile
        Return None if no customer id is found.
        :param gateway_id: The gateway ID to which the customer id is associated
        """
        PaymentProfile = Pool().get('party.payment_profile')

        payment_profiles = PaymentProfile.search([
            ('party', '=', self.id),
            ('stripe_customer_id', '!=', None),
            ('gateway', '=', gateway_id),
        ])
        if payment_profiles:
            return payment_profiles[0].stripe_customer_id
        return None
