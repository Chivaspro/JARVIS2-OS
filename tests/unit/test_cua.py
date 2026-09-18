import asyncio

from cua_driver import (
    CaptureScope,
    CuaDriver,
    EndSessionInput,
    GetDesktopStateInput,
    StartSessionInput,
)


async def main() -> None:
    driver = CuaDriver.create()
    session = "jarvis-cua-test"

    await driver.start_session(
        StartSessionInput(
            session=session,
            capture_scope=CaptureScope.DESKTOP,
        )
    )

    try:
        result = await driver.get_desktop_state(
            GetDesktopStateInput(
                session=session,
                screenshot_out_file=None,
            )
        )

        if result.is_error:
            raise RuntimeError(result.text)

        print("CUA DESKTOP TEST: PASS")
        print("Image type:", result.images[0].mime_type)

    finally:
        await driver.end_session(
            EndSessionInput(session=session)
        )
        await driver.shutdown()


if __name__ == "__main__":
    asyncio.run(main())