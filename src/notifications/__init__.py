"""Email notifications (WP-44) - practical pub-sub without a broker.

``mailer.py`` sends; ``digest.py`` decides who gets what and when (a
scheduled digest per subscriber frequency, or an immediate alert for
sweep/scan failures). See ``src/storage/notifications.py`` for the
subscriber table and small kv sending state both modules share.
"""
