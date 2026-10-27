def test_auth_me_returns_current_user_and_linked_profile(api_client, registered_account, access_token):
    response = api_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    assert response.json()["email"] == registered_account["email"]
    assert response.json()["profile"]["user_id"] == registered_account["id"]


def test_auth_me_allows_account_without_linked_profile(api_client, registered_account, access_token):
    api_client.app.state.db.table("profiles").remove(lambda item: item["user_id"] == registered_account["id"])

    response = api_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    assert response.json()["profile"] is None