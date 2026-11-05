"""Exercise guest forms as browsers do; inspect HttpOnly cookies only in tests."""

import re


def guest_csrf(response):
    match = re.search(r'data-guest-csrf-token="([0-9a-f]+)"', response.text)
    assert match is not None, response.text
    return match.group(1)


def response_session_id(response):
    cookies = [(key, value) for key, value in response.cookies.items() if key.startswith("guest_session_")]
    assert len(cookies) == 1, response.headers
    return cookies[0][1].split(".")[0]


async def password_form(client, path, password):
    gate = await client.get(path)
    return await client.post(
        f"{path}/verify-password", data={"password": password, "guest_csrf_token": guest_csrf(gate)}
    )
