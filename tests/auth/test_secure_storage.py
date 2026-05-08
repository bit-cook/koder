"""Tests for secure credential storage."""

import subprocess
from unittest.mock import MagicMock, patch

from koder_agent.auth.secure_storage import SecureStorage, get_storage


class TestSecureStorage:
    """Test SecureStorage class."""

    @patch("shutil.which")
    @patch("platform.system")
    def test_is_available_returns_true_on_macos(self, mock_system, mock_which):
        """Test is_available returns True on macOS when security command exists."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"

        storage = SecureStorage()
        assert storage.is_available() is True

        mock_system.assert_called_once_with()
        mock_which.assert_called_once_with("security")

    @patch("shutil.which")
    @patch("platform.system")
    def test_is_available_returns_false_when_security_not_found(self, mock_system, mock_which):
        """Test is_available returns False when security command not found."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = None

        storage = SecureStorage()
        assert storage.is_available() is False

    @patch("shutil.which")
    @patch("platform.system")
    def test_is_available_returns_false_on_non_macos(self, mock_system, mock_which):
        """Test is_available returns False on non-macOS platforms."""
        mock_system.return_value = "Linux"
        mock_which.return_value = "/usr/bin/security"

        storage = SecureStorage()
        assert storage.is_available() is False

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_store_calls_subprocess_with_correct_args(self, mock_system, mock_which, mock_run):
        """Test store calls subprocess with correct arguments."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.return_value = MagicMock(returncode=0)

        storage = SecureStorage()
        result = storage.store("koder", "api_key", "secret-key-123")

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "security"
        assert "add-generic-password" in args
        assert "-s" in args
        assert "koder" in args
        assert "-a" in args
        assert "api_key" in args
        assert "-w" in args
        assert "secret-key-123" in args
        assert "-U" in args  # Update if exists

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_store_returns_false_on_error(self, mock_system, mock_which, mock_run):
        """Test store returns False on subprocess error."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.return_value = MagicMock(returncode=1)

        storage = SecureStorage()
        result = storage.store("koder", "api_key", "secret-key-123")

        assert result is False

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_retrieve_calls_subprocess_with_correct_args(self, mock_system, mock_which, mock_run):
        """Test retrieve calls subprocess with correct arguments."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.return_value = MagicMock(returncode=0, stdout="secret-key-123\n", stderr="")

        storage = SecureStorage()
        result = storage.retrieve("koder", "api_key")

        assert result == "secret-key-123"
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "security"
        assert "find-generic-password" in args
        assert "-s" in args
        assert "koder" in args
        assert "-a" in args
        assert "api_key" in args
        assert "-w" in args

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_retrieve_returns_none_on_error(self, mock_system, mock_which, mock_run):
        """Test retrieve returns None on error."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.side_effect = subprocess.CalledProcessError(1, "security")

        storage = SecureStorage()
        result = storage.retrieve("koder", "api_key")

        assert result is None

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_retrieve_returns_none_when_not_found(self, mock_system, mock_which, mock_run):
        """Test retrieve returns None when credential not found."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.return_value = MagicMock(returncode=44)  # errSecItemNotFound

        storage = SecureStorage()
        result = storage.retrieve("koder", "nonexistent")

        assert result is None

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_delete_calls_subprocess_with_correct_args(self, mock_system, mock_which, mock_run):
        """Test delete calls subprocess with correct arguments."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.return_value = MagicMock(returncode=0)

        storage = SecureStorage()
        result = storage.delete("koder", "api_key")

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "security"
        assert "delete-generic-password" in args
        assert "-s" in args
        assert "koder" in args
        assert "-a" in args
        assert "api_key" in args

    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("platform.system")
    def test_delete_returns_false_on_error(self, mock_system, mock_which, mock_run):
        """Test delete returns False on error."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"
        mock_run.return_value = MagicMock(returncode=1)

        storage = SecureStorage()
        result = storage.delete("koder", "api_key")

        assert result is False


class TestGetStorage:
    """Test get_storage function."""

    @patch("shutil.which")
    @patch("platform.system")
    def test_get_storage_returns_secure_storage_on_macos(self, mock_system, mock_which):
        """Test get_storage returns SecureStorage on macOS."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = "/usr/bin/security"

        storage = get_storage()

        assert storage is not None
        assert isinstance(storage, SecureStorage)
        assert storage.is_available() is True

    @patch("shutil.which")
    @patch("platform.system")
    def test_get_storage_returns_none_on_linux(self, mock_system, mock_which):
        """Test get_storage returns None on Linux."""
        mock_system.return_value = "Linux"
        mock_which.return_value = None

        storage = get_storage()

        assert storage is None

    @patch("shutil.which")
    @patch("platform.system")
    def test_get_storage_returns_none_on_windows(self, mock_system, mock_which):
        """Test get_storage returns None on Windows."""
        mock_system.return_value = "Windows"
        mock_which.return_value = None

        storage = get_storage()

        assert storage is None

    @patch("shutil.which")
    @patch("platform.system")
    def test_get_storage_returns_none_when_security_unavailable(self, mock_system, mock_which):
        """Test get_storage returns None when security command unavailable."""
        mock_system.return_value = "Darwin"
        mock_which.return_value = None

        storage = get_storage()

        assert storage is None
