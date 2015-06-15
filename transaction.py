# -*- coding: utf-8 -*-
"""
    transaction.py

    :copyright: (c) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
import stripe

from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, Bool, Not
from trytond.model import fields
from trytond.exceptions import UserError

__metaclass__ = PoolMeta
__all__ = [
    'PaymentGatewayStripe', 'PaymentTransactionStripe', 'AddPaymentProfileView',
    'AddPaymentProfile'
]


class PaymentGatewayStripe:
    "Stripe Gateway Implementation"
    __name__ = 'payment_gateway.gateway'

    stripe_api_key_test = fields.Char(
        'Stripe Test API Key', states={
            'required': Eval('provider') == 'stripe',
            'invisible': Eval('provider') != 'stripe',
            'readonly': Not(Bool(Eval('active'))),
        }, depends=['provider', 'active']
    )
    stripe_api_key_live = fields.Char(
        'Stripe Live API Key', states={
            'required': Eval('provider') == 'stripe',
            'invisible': Eval('provider') != 'stripe',
            'readonly': Not(Bool(Eval('active'))),
        }, depends=['provider', 'active']
    )

    @classmethod
    def get_providers(cls, values=None):
        """
        Downstream modules can add to the list
        """
        rv = super(PaymentGatewayStripe, cls).get_providers()
        stripe_record = ('stripe', 'Stripe')
        if stripe_record not in rv:
            rv.append(stripe_record)
        return rv

    def get_methods(self):
        if self.provider == 'stripe':
            return [
                ('credit_card', 'Credit Card - Stripe'),
            ]
        return super(PaymentGatewayStripe, self).get_methods()

    def get_stripe_api_key(self):
        """
        Return the api key to use for stripe

        Return test key if the gateway is used for test, else the live key
        """
        assert self.provider == 'stripe', 'Invalid provider'

        return self.stripe_api_key_test if self.test \
            else self.stripe_api_key_live


class PaymentTransactionStripe:
    """
    Payment Transaction implementation for Stripe
    """
    __name__ = 'payment_gateway.transaction'

    def authorize_stripe(self, card_info=None):
        """
        Authorize using stripe.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        stripe.api_key = self.gateway.get_stripe_api_key()

        charge_data = self.get_stripe_charge_data(card_info=card_info)
        charge_data['capture'] = False

        try:
            charge = stripe.Charge.create(**charge_data)
        except (
            stripe.error.CardError, stripe.error.InvalidRequestError,
            stripe.error.AuthenticationError, stripe.error.APIConnectionError,
            stripe.error.StripeError
        ), exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.json_body)
        else:
            self.state = 'authorized'
            self.provider_reference = charge.id
            self.save()
            TransactionLog.serialize_and_create(self, charge)

    def settle_stripe(self):
        """
        Settle an authorized charge
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        assert self.state == 'authorized'

        stripe.api_key = self.gateway.get_stripe_api_key()

        try:
            charge = stripe.Charge.retrieve(self.provider_reference)
            charge.capture(amount=int(self.amount * 100))
        except (
            stripe.error.InvalidRequestError,
            stripe.error.AuthenticationError, stripe.error.APIConnectionError,
            stripe.error.StripeError
        ), exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.json_body)
        else:
            self.state = 'completed'
            self.provider_reference = charge.id
            self.save()
            TransactionLog.serialize_and_create(self, charge)
            self.safe_post()

    def capture_stripe(self, card_info=None):
        """
        Capture using stripe.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        stripe.api_key = self.gateway.get_stripe_api_key()

        charge_data = self.get_stripe_charge_data(card_info=card_info)
        charge_data['capture'] = True

        try:
            charge = stripe.Charge.create(**charge_data)
        except (
            stripe.error.CardError, stripe.error.InvalidRequestError,
            stripe.error.AuthenticationError, stripe.error.APIConnectionError,
            stripe.error.StripeError
        ), exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.json_body)
        else:
            self.state = 'completed'
            self.provider_reference = charge.id
            self.save()
            TransactionLog.serialize_and_create(self, charge)
            self.safe_post()

    def get_stripe_charge_data(self, card_info=None):
        """
        Downstream modules can modify this method to send extra data to
        stripe
        """
        charge_data = {
            'amount': int(self.amount * 100),
            'currency': self.currency.code.lower(),
        }

        if card_info:
            charge_data['source'] = {
                'object': 'card',
                'number': card_info.number,
                'exp_month': card_info.expiry_month,
                'exp_year': card_info.expiry_year,
                'cvc': card_info.csc,
                'name': card_info.owner or self.address.name or self.party.name
            }
            charge_data['source'].update(self.address.get_address_for_stripe())

        elif self.payment_profile:
            charge_data.update({
                'customer': self.payment_profile.stripe_customer_id,
                'card': self.payment_profile.provider_reference,
            })

        else:
            self.raise_user_error('no_card_or_profile')

        return charge_data

    def retry_stripe(self, credit_card=None):
        """
        Retry charge

        :param credit_card: An instance of CreditCardView
        """
        raise self.raise_user_error('feature_not_available')

    def update_stripe(self):
        """
        Update the status of the transaction from Stripe
        """
        raise self.raise_user_error('feature_not_available')

    def cancel_stripe(self):
        """
        Cancel this authorization or request
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        if self.state != 'authorized':
            self.raise_user_error('cancel_only_authorized')

        stripe.api_key = self.gateway.get_stripe_api_key()

        try:
            charge = stripe.Charge.retrieve(self.provider_reference).refund()
        except (
            stripe.error.InvalidRequestError,
            stripe.error.AuthenticationError, stripe.error.APIConnectionError,
            stripe.error.StripeError
        ), exc:
            TransactionLog.serialize_and_create(self, exc.json_body)
        else:
            self.state = 'cancel'
            self.save()
            TransactionLog.serialize_and_create(self, charge)


class AddPaymentProfileView:
    __name__ = 'party.payment_profile.add_view'

    @classmethod
    def get_providers(cls):
        """
        Add Stripe to the list of providers who support payment profiles.
        """
        res = super(AddPaymentProfileView, cls).get_providers()
        res.append(('stripe', 'Stripe'))
        return res


class AddPaymentProfile:
    """
    Add a payment profile
    """
    __name__ = 'party.party.payment_profile.add'

    def transition_add_stripe(self):
        """
        Handle the case if the profile should be added for Stripe
        """
        card_info = self.card_info

        stripe.api_key = card_info.gateway.get_stripe_api_key()

        profile_data = {
            'source': {
                'object': 'card',
                'number': card_info.number,
                'exp_month': card_info.expiry_month,
                'exp_year': card_info.expiry_year,
                'cvc': card_info.csc,
                'name': (
                    card_info.owner or self.address.name or self.party.name
                ),
            },
        }
        profile_data['source'].update(
            card_info.address.get_address_for_stripe())

        customer_id = card_info.party._get_stripe_customer_id(
            card_info.gateway.id
        )

        try:
            if customer_id:
                customer = stripe.Customer.retrieve(customer_id)
                card = customer.sources.create(**profile_data)
            else:
                profile_data.update({
                    'description': card_info.party.name,
                    'email': card_info.party.email,
                })
                customer = stripe.Customer.create(**profile_data)
                card = customer.sources.data[0]
        except (
            stripe.error.CardError, stripe.error.InvalidRequestError,
            stripe.error.AuthenticationError, stripe.error.APIConnectionError,
            stripe.error.StripeError
        ), exc:
            raise UserError(exc.json_body['error']['message'])

        return self.create_profile(
            card.id,
            stripe_customer_id=customer.id
        )
