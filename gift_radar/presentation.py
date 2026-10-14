from html import escape

from .models import utcnow

FEE_LABELS = {"buy": "Комиссия покупки", "sell": "Комиссия продажи", "transfer": "Передача подарка",
              "withdraw": "Вывод", "network": "Сеть", "conversion": "Конвертация", "other": "Прочие расходы"}


def amount(value, currency=""):
    if value is None:
        return "неизвестно"
    return f"{value:.4f}".rstrip("0").rstrip(".") + (f" {currency}" if currency else "")


def text(value):
    # Truncate before escaping, so an HTML entity is never split.
    return escape(str(value)[:150]) if value is not None else "неизвестно"


def display_money(value, currency, targets, store, now, user=0):
    parts = [amount(value, currency)]
    for target in targets:
        if target == currency:
            continue
        rate = store.rate(currency, target, user)
        converted = rate.convert(value, now) if rate and value is not None else None
        if converted is None:
            parts.append(f"{target}: конвертация недоступна (курс отсутствует/устарел)")
        else:
            parts.append(f"≈ {amount(converted, target)}; курс {amount(rate.value)}, {text(rate.source)}, {rate.at:%d.%m %H:%M UTC}")
    return "\n".join(parts)


def render_card(value, settings, store, now=None, user=0):
    now = now or utcnow()
    x = value.listing
    signature = "Есть" if x.signature is True else "Нет" if x.signature is False else "Не удалось проверить"
    lines = ["<b>🎁 Возможный кандидат для проверки</b>",
             f"Название: {text(x.name)}", f"Коллекция: {text(x.collection)}",
             f"Модель: {text(x.model)} · Фон: {text(x.backdrop)}",
             f"Узор: {text(x.pattern)} · Символ: {text(x.symbol)} · Номер: {text(x.serial)}",
             f"Редкое свойство: {'да' if x.rare is True else 'нет' if x.rare is False else 'неизвестно'}",
             f"Подпись на подарке: {signature}"]
    if x.signature_text:
        lines.append(f"⚠️ Подпись: {text(x.signature_text)}")
    lines += [f"Покупка: {text(x.market)} → перепродажа: {text(value.target)}",
              f"Цена: {display_money(x.price, x.currency, settings.display_currencies, store, now, user)}",
              f"Данные обновлены: {x.updated_at:%d.%m %H:%M UTC}",
              f"Источник: {text(x.source)}", "", "<b>Завершённые продажи — найденные аналоги</b>"]
    for label, stats in (("24 часа", value.day), (f"{value.period_days} дней", value.period)):
        if stats.count < settings.min_sales:
            lines.append(f"{label}: {stats.count}; недостаточно для надёжной статистики")
        else:
            lines.append(f"{label}: {stats.count}; медиана {amount(stats.median, x.currency)}, диапазон {amount(stats.minimum)}–{amount(stats.maximum)}")
    if value.period.last:
        last = value.period.last
        lines.append(f"Последняя: {last.sold_at:%d.%m %H:%M UTC}, {amount(last.gift.price, x.currency)}")
    lines += [f"Ликвидность: {value.liquidity}", f"Уверенность: {value.confidence}",
              f"Активные объявления аналогов: {value.active_count if value.active_count is not None else 'нет полного снимка рынка'}"]
    if value.period.median_hours is not None:
        lines.append(f"Медианное время прошлых продаж: {amount(value.period.median_hours)} ч (только лоты с известным временем размещения)")
    if value.scenarios:
        lines += ["", "<b>Расчёт по истории продаж</b>"]
        for scenario in value.scenarios:
            lines.append(f"{scenario.name}: продажа {amount(scenario.resale, x.currency)}; чистое поступление {amount(scenario.proceeds)}; прибыль {amount(scenario.profit)}; ROI {amount(scenario.roi)}{'%' if scenario.roi is not None else ''}")
        if value.scenarios[1].profit is not None:
            lines.append("Медианная прибыль в выбранных валютах (приблизительно):\n" + display_money(value.scenarios[1].profit, x.currency, settings.display_currencies, store, now, user))
        base = value.scenarios[1]
        lines += ["", "<b>Расходы медианного сценария</b>", f"Покупка: {amount(x.price, x.currency)}"]
        for name, cost in base.expenses.items():
            lines.append(f"{FEE_LABELS[name]}: {amount(cost, x.currency)}")
        lines += [f"Все расходы приобретения: {amount(base.acquisition, x.currency)}",
                  "База ROI = покупка + комиссия покупки + передача + сеть + конвертация + прочее. Продажа и вывод вычитаются из поступления."]
        for warning in dict.fromkeys(base.warnings):
            lines.append(f"⚠️ {text(warning)}")
    else:
        lines.append("Расчёт перепродажи недоступен: недостаточно сопоставимых завершённых продаж.")
    for warning in value.warnings:
        lines.append(f"⚠️ {text(warning)}")
    if (now - x.updated_at).total_seconds() > 1800:
        lines.append("⚠️ Лот не обновлялся более 30 минут; уведомления по нему заблокированы.")
    lines.append("⚠️ Историческая оценка не гарантирует прибыль или срок продажи. Экспорт может быть неполным.")
    return "\n".join(lines)


def comparison(value):
    s = value.scenarios[1] if value.scenarios else None
    return f"{text(value.target)}: аналоги {value.period.count}, медиана {amount(value.period.median, value.listing.currency)}; прибыль {amount(s.profit if s else None)}; уверенность {value.confidence}"


def settings_text(s):
    return (f"Валюта порогов: {s.calculation_currency}\nМинимальная чистая прибыль: {s.min_profit}\n"
            f"Минимальный ROI: {s.min_roi}%\nЦена покупки до: {s.max_price}\n"
            f"Минимум аналогов: {s.min_sales}\nЛиквидность: {s.min_liquidity}\n"
            f"Подпись: {s.signature}\nИстория: {s.history_days} дней + обязательные 24 часа\n"
            f"Покупка: {', '.join(s.buy_markets)}\nПерепродажа: {', '.join(s.sell_markets)}\n"
            f"Коллекции: {', '.join(s.collections) or 'любые'}\nМодели: {', '.join(s.models) or 'любые'}\n"
            f"Фоны: {', '.join(s.backdrops) or 'любые'}\nУзоры: {', '.join(s.patterns) or 'любые'}\nСимволы: {', '.join(s.symbols) or 'любые'}\n"
            f"Показ валют: {', '.join(s.display_currencies)}\n"
            f"Скидка подписи: {str(s.signature_discount)+'% (пользовательская)' if s.signature_discount is not None else 'не задана'}\n"
            f"Уведомления: {'включены' if s.alerts else 'выключены'}; интервал {s.interval_minutes} мин, до 3 карточек за цикл.\n"
            "Неизвестные расходы, слабые данные и устаревшие лоты блокируют уведомления.")
