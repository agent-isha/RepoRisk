"""Shared helpers for tests that need a signed-in API client."""

TEST_EMAIL = "analyst@example.com"
TEST_PASSWORD = "correct horse battery staple"  # test-only credential, never used outside tests


def sign_in(client, app, email=TEST_EMAIL, password=TEST_PASSWORD):
    """Create the account if needed, log in, and send the CSRF token on later requests."""
    store = app.state.auth
    if store.get_user(email) is None:
        store.create_user(email, password)
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    session = response.json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    return session
