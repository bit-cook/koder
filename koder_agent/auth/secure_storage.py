"""Secure credential storage for macOS Keychain."""

import platform
import shutil
import subprocess
from typing import Optional


class SecureStorage:
    """Secure credential storage using macOS Keychain."""

    def is_available(self) -> bool:
        """
        Check if secure storage is available on this platform.

        Returns:
            bool: True if macOS Keychain is available, False otherwise.
        """
        if platform.system() != "Darwin":
            return False
        return shutil.which("security") is not None

    def store(self, service: str, account: str, data: str) -> bool:
        """
        Store a credential in macOS Keychain.

        Args:
            service: The service name (e.g., "koder")
            account: The account name (e.g., "api_key")
            data: The credential data to store

        Returns:
            bool: True if successful, False otherwise.
        """
        if not self.is_available():
            return False

        try:
            result = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-s",
                    service,
                    "-a",
                    account,
                    "-w",
                    data,
                    "-U",  # Update if already exists
                ],
                capture_output=True,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def retrieve(self, service: str, account: str) -> Optional[str]:
        """
        Retrieve a credential from macOS Keychain.

        Args:
            service: The service name (e.g., "koder")
            account: The account name (e.g., "api_key")

        Returns:
            str: The credential data if found, None otherwise.
        """
        if not self.is_available():
            return None

        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    service,
                    "-a",
                    account,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except subprocess.CalledProcessError:
            return None
        except Exception:
            return None

    def delete(self, service: str, account: str) -> bool:
        """
        Delete a credential from macOS Keychain.

        Args:
            service: The service name (e.g., "koder")
            account: The account name (e.g., "api_key")

        Returns:
            bool: True if successful, False otherwise.
        """
        if not self.is_available():
            return False

        try:
            result = subprocess.run(
                [
                    "security",
                    "delete-generic-password",
                    "-s",
                    service,
                    "-a",
                    account,
                ],
                capture_output=True,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False


def get_storage() -> Optional[SecureStorage]:
    """
    Get secure storage instance if available.

    Returns:
        SecureStorage instance on macOS, None on other platforms.
    """
    storage = SecureStorage()
    if storage.is_available():
        return storage
    return None
