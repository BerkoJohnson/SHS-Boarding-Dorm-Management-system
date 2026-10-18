"""Public marketing website for Eden Creative Systems (Eden Sys).

This app is registered in ``settings.PUBLIC_APPS`` so that its tables live in
the PostgreSQL ``public`` schema. It is served from the ``public`` URLconf
(``schoolsystem/urls_public.py``) on the host(s) listed in
``settings.PUBLIC_WEBSITE_DOMAIN``. It contains no school data and cannot
reach any tenant schema.
"""
