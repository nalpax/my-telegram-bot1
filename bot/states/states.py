from aiogram.fsm.state import State, StatesGroup


class CategoryStates(StatesGroup):
    waiting_name = State()  # add
    waiting_rename = State()
    waiting_description = State()
    waiting_photo = State()


class ProductStates(StatesGroup):
    add_name = State()
    add_description = State()
    add_price = State()
    add_photo = State()

    edit_name = State()
    edit_description = State()
    edit_price = State()
    waiting_photo = State()


class DigitalItemStates(StatesGroup):
    waiting_items = State()


class PromoCodeStates(StatesGroup):
    waiting_code = State()
    waiting_discount = State()
    waiting_max_uses = State()
    waiting_expires = State()


class BroadcastStates(StatesGroup):
    waiting_message = State()
    waiting_confirm = State()


class CatalogPhotoStates(StatesGroup):
    waiting_photo = State()


class PurchaseStates(StatesGroup):
    waiting_promo = State()


class SubscriptionSettingsStates(StatesGroup):
    waiting_channel = State()
    waiting_url = State()


class SearchStates(StatesGroup):
    waiting_query = State()


class UserSearchStates(StatesGroup):
    waiting_query = State()


class TextSettingStates(StatesGroup):
    waiting_value = State()
