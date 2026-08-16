"""api_routes — domain routers extracted from api.py (roadmap #3 split).

api.py mounts each router (same paths as before the split) and re-exports
every handler + model so `import api; api.search(...)` keeps working.
"""
