models_as_jsonschema = {
    'social-accounts': {'properties': {
        'socialId': {'type': 'string'},
        'chatId': {'type': 'string'},
        'platform': {'type': 'string'},
        'firstName': {'type': ['string', 'null']},
        'lastName': {'type': ['string', 'null']},
        'extraData': {
                'type': 'object',
                'properties': {
                    'nickname': {'type': ['string', 'null']},
                    'domain': {'type': ['string', 'null']},
                    'bdate': {'type': ['string', 'null']},
                    'city': {'type': ['object', 'null']},
                    'email': {'type': ['string', 'null']},
                    'mobile_phone': {'type': ['string', 'null']},
                    'photo_200': {'type': ['string', 'null']},
                    'screen_name': {'type': ['string', 'null']},
                    'sex': {'type': ['integer', 'null']},
                    'timezone': {'type': ['integer', 'null']},
                    'username': {'type': ['string', 'null']},  # Учитываем, что username может быть None
                    'language_code': {'type': ['string', 'null']},
                    'is_premium': {'type': ['boolean', 'null']},
                    'added_to_attachment_menu': {'type': ['boolean', 'null']},
                },
                'additionalProperties': True,  # Разрешаем дополнительные поля
            },
        'nonce': {'type': ['string', 'null']},
        'user': {'relation': 'to-one', 'resource': ['users']},
    }},

    'users': {'properties': {
        'firstName': {'type': 'string'},
        'lastName': {'type': ['string', 'null']},
        'username': {'type': 'string'},
        'notifyScheduleUpdates': {'type': 'boolean'},
        'notifyUpcomingLessons': {'type': 'boolean'},
        'subscriptions': {'relation': 'to-many', 'resource': ['group-subscriptions', 'teacher-subscriptions']},
        'accounts': {'relation': 'to-many', 'resource': ['accounts']},
    }},

    "group-subscriptions": {
        "properties": {
            'createdAt': {'type': ['string', 'null']},
            'updatedAt': {'type': ['string', 'null']},
            "user": {
                "relation": "to-one",
                "resource": ["users"],
            },
            "group": {
                "relation": "to-one",
                "resource": ["groups"],
            }
        }
    },
    "teacher-subscriptions": {
        "properties": {
            'createdAt': {'type': ['string', 'null']},
            'updatedAt': {'type': ['string', 'null']},
            "user": {
                "relation": "to-one",
                "resource": ["users"],
            },
            "teacher": {
                "relation": "to-one",
                "resource": ["teachers"],
            }
        }
    },

}
