"""Tests for telemetry configuration."""

from unittest.mock import Mock, patch


def test_get_tracer():
    """Test getting a tracer instance."""
    from stampbot.telemetry import get_tracer

    tracer = get_tracer("test_module")
    assert tracer is not None


def test_set_span_error_with_none():
    """Test set_span_error does nothing when span is None."""
    from stampbot.telemetry import set_span_error

    # Should not raise
    set_span_error(None, Exception("test error"))


def test_set_span_error_with_span():
    """Test set_span_error sets error status on span."""
    from stampbot.telemetry import set_span_error

    mock_span = Mock()
    error = Exception("test error")

    set_span_error(mock_span, error)

    mock_span.record_exception.assert_called_once_with(error)
    mock_span.set_status.assert_called_once()


def test_set_span_ok_with_none():
    """Test set_span_ok does nothing when span is None."""
    from stampbot.telemetry import set_span_ok

    # Should not raise
    set_span_ok(None)


def test_set_span_ok_with_span():
    """Test set_span_ok sets OK status on span."""
    from stampbot.telemetry import set_span_ok

    mock_span = Mock()

    set_span_ok(mock_span)

    mock_span.set_status.assert_called_once()


def test_add_span_attributes_with_none():
    """Test add_span_attributes does nothing when span is None."""
    from stampbot.telemetry import add_span_attributes

    # Should not raise
    add_span_attributes(None, {"key": "value"})


def test_add_span_attributes_with_span():
    """Test add_span_attributes adds attributes to span."""
    from stampbot.telemetry import add_span_attributes

    mock_span = Mock()
    attributes = {"key1": "value1", "key2": "value2"}

    add_span_attributes(mock_span, attributes)

    assert mock_span.set_attribute.call_count == 2
    mock_span.set_attribute.assert_any_call("key1", "value1")
    mock_span.set_attribute.assert_any_call("key2", "value2")


def test_create_span_when_otel_disabled():
    """Test create_span yields None when OTEL is disabled."""
    with patch("stampbot.telemetry.settings") as mock_settings:
        mock_settings.otel_enabled = False
        from stampbot.telemetry import create_span

        with create_span("test_span", {"attr": "value"}) as span:
            assert span is None


def test_configure_telemetry_disabled():
    """Test configure_telemetry returns None when disabled."""
    with patch("stampbot.telemetry.settings") as mock_settings:
        mock_settings.otel_enabled = False
        from stampbot.telemetry import configure_telemetry

        result = configure_telemetry()
        assert result is None


def test_configure_telemetry_no_endpoint():
    """Test configure_telemetry returns None when no endpoint configured."""
    with patch("stampbot.telemetry.settings") as mock_settings:
        mock_settings.otel_enabled = True
        mock_settings.otel_endpoint = None
        from stampbot.telemetry import configure_telemetry

        result = configure_telemetry()
        assert result is None


def test_instrument_fastapi_when_disabled():
    """Test instrument_fastapi does nothing when OTEL is disabled."""
    with patch("stampbot.telemetry.settings") as mock_settings:
        mock_settings.otel_enabled = False
        from stampbot.telemetry import instrument_fastapi

        mock_app = Mock()
        # Should not raise and should not call instrumentor
        instrument_fastapi(mock_app)


def test_configure_telemetry_success():
    """Test configure_telemetry returns provider when properly configured."""
    from stampbot.version import APP_VERSION

    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.Resource") as mock_resource,
        patch("stampbot.telemetry.TracerProvider") as mock_provider_cls,
        patch("stampbot.telemetry.OTLPSpanExporter") as mock_exporter,
        patch("stampbot.telemetry.BatchSpanProcessor") as mock_processor,
        patch("stampbot.telemetry.trace") as mock_trace,
    ):
        mock_settings.otel_enabled = True
        mock_settings.otel_endpoint = "http://localhost:4317"
        mock_settings.otel_service_name = "test-service"
        mock_settings.get.return_value = False

        mock_provider = Mock()
        mock_provider_cls.return_value = mock_provider

        from stampbot.telemetry import configure_telemetry

        result = configure_telemetry()

        assert result == mock_provider
        mock_resource.create.assert_called_once_with(
            {"service.name": "test-service", "service.version": APP_VERSION}
        )
        mock_provider_cls.assert_called_once()
        mock_exporter.assert_called_once_with(
            endpoint="http://localhost:4317",
            insecure=False,
        )
        mock_processor.assert_called_once()
        mock_trace.set_tracer_provider.assert_called_once_with(mock_provider)


def test_configure_telemetry_plaintext_requires_opt_in():
    """Test plaintext OTLP is used only when explicitly enabled."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.Resource"),
        patch("stampbot.telemetry.TracerProvider") as mock_provider_cls,
        patch("stampbot.telemetry.OTLPSpanExporter") as mock_exporter,
        patch("stampbot.telemetry.BatchSpanProcessor"),
        patch("stampbot.telemetry.trace"),
    ):
        mock_settings.otel_enabled = True
        mock_settings.otel_endpoint = "http://otel-collector:4317"
        mock_settings.otel_service_name = "test-service"
        mock_settings.get.return_value = True
        mock_provider_cls.return_value = Mock()

        from stampbot.telemetry import configure_telemetry

        configure_telemetry()

        mock_exporter.assert_called_once_with(
            endpoint="http://otel-collector:4317",
            insecure=True,
        )


def test_configure_telemetry_rejects_non_boolean_plaintext_setting():
    """Test a malformed insecure setting cannot disable TLS."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.Resource"),
        patch("stampbot.telemetry.TracerProvider") as mock_provider_cls,
        patch("stampbot.telemetry.OTLPSpanExporter") as mock_exporter,
        patch("stampbot.telemetry.BatchSpanProcessor"),
        patch("stampbot.telemetry.trace"),
    ):
        mock_settings.otel_enabled = True
        mock_settings.otel_endpoint = "http://otel-collector:4317"
        mock_settings.otel_service_name = "test-service"
        mock_settings.get.return_value = "true"
        mock_provider_cls.return_value = Mock()

        from stampbot.telemetry import configure_telemetry

        configure_telemetry()

        mock_exporter.assert_called_once_with(
            endpoint="http://otel-collector:4317",
            insecure=False,
        )


def test_configure_telemetry_https_endpoint_cannot_be_downgraded():
    """Test an HTTPS endpoint keeps TLS when plaintext is requested."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.Resource"),
        patch("stampbot.telemetry.TracerProvider") as mock_provider_cls,
        patch("stampbot.telemetry.OTLPSpanExporter") as mock_exporter,
        patch("stampbot.telemetry.BatchSpanProcessor"),
        patch("stampbot.telemetry.trace"),
    ):
        mock_settings.otel_enabled = True
        mock_settings.otel_endpoint = "https://otel-collector:4317"
        mock_settings.otel_service_name = "test-service"
        mock_settings.get.return_value = True
        mock_provider_cls.return_value = Mock()

        from stampbot.telemetry import configure_telemetry

        configure_telemetry()

        mock_exporter.assert_called_once_with(
            endpoint="https://otel-collector:4317",
            insecure=False,
        )


def test_configure_telemetry_exception():
    """Test configure_telemetry handles exceptions gracefully."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.Resource") as mock_resource,
    ):
        mock_settings.otel_enabled = True
        mock_settings.otel_endpoint = "http://localhost:4317"
        mock_resource.create.side_effect = Exception("Connection failed")

        from stampbot.telemetry import configure_telemetry

        result = configure_telemetry()
        assert result is None


def test_instrument_fastapi_success():
    """Test instrument_fastapi instruments app when OTEL is enabled."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.FastAPIInstrumentor") as mock_instrumentor,
    ):
        mock_settings.otel_enabled = True

        from stampbot.telemetry import instrument_fastapi

        mock_app = Mock()
        instrument_fastapi(mock_app)

        mock_instrumentor.instrument_app.assert_called_once_with(mock_app)


def test_instrument_fastapi_exception():
    """Test instrument_fastapi handles exceptions gracefully."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.FastAPIInstrumentor") as mock_instrumentor,
    ):
        mock_settings.otel_enabled = True
        mock_instrumentor.instrument_app.side_effect = Exception("Instrumentation failed")

        from stampbot.telemetry import instrument_fastapi

        mock_app = Mock()
        # Should not raise
        instrument_fastapi(mock_app)


def test_create_span_with_exception():
    """Test create_span records exception when one is raised."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.get_tracer") as mock_get_tracer,
    ):
        mock_settings.otel_enabled = True

        mock_span = Mock()
        mock_tracer = Mock()
        mock_tracer.start_as_current_span.return_value.__enter__ = Mock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = Mock(return_value=False)
        mock_get_tracer.return_value = mock_tracer

        from stampbot.telemetry import create_span

        test_error = ValueError("test error")
        try:
            with create_span("test_span"):
                raise test_error
        except ValueError:
            pass

        mock_span.record_exception.assert_called_once_with(test_error)
        mock_span.set_status.assert_called_once()


def test_create_span_with_attributes_when_enabled():
    """Test create_span sets attributes on span when OTEL is enabled."""
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.get_tracer") as mock_get_tracer,
    ):
        mock_settings.otel_enabled = True

        mock_span = Mock()
        mock_tracer = Mock()
        mock_tracer.start_as_current_span.return_value.__enter__ = Mock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = Mock(return_value=False)
        mock_get_tracer.return_value = mock_tracer

        from stampbot.telemetry import create_span

        with create_span("test_span", {"key1": "value1", "key2": "value2"}) as span:
            assert span == mock_span

        # Verify attributes were set on the span
        assert mock_span.set_attribute.call_count == 2
        mock_span.set_attribute.assert_any_call("key1", "value1")
        mock_span.set_attribute.assert_any_call("key2", "value2")


def test_create_span_with_exception_record_disabled():
    """Test create_span does NOT record exception when record_exception=False.

    This tests the branch where record_exception is False (line 148->151).
    The exception should still be raised but not recorded on the span.
    """
    with (
        patch("stampbot.telemetry.settings") as mock_settings,
        patch("stampbot.telemetry.get_tracer") as mock_get_tracer,
    ):
        mock_settings.otel_enabled = True

        mock_span = Mock()
        mock_tracer = Mock()
        mock_tracer.start_as_current_span.return_value.__enter__ = Mock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = Mock(return_value=False)
        mock_get_tracer.return_value = mock_tracer

        from stampbot.telemetry import create_span

        test_error = ValueError("test error")
        try:
            with create_span("test_span", record_exception=False):
                raise test_error
        except ValueError:
            pass

        # Verify exception was NOT recorded on the span
        mock_span.record_exception.assert_not_called()
        mock_span.set_status.assert_not_called()
