from hypothesis import settings

# No example database on disk and no timing deadline, so runs leave no files behind and slow CI machines pass.
settings.register_profile("brokerclock", database=None, deadline=None)
settings.load_profile("brokerclock")
