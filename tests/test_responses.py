"""Tests for the GraphQL response envelope parse layer (responses.py)."""


class TestSrvEnvelopeWithConfigRepoUser:
    """SERVICES_QUERY now requests configRepoUser; envelope must accept it."""

    def test_parses_full_xssrv_with_partitions(self) -> None:
        from verisure_italy.responses import ServicesEnvelope

        envelope = ServicesEnvelope.model_validate(
            {
                "data": {
                    "xSSrv": {
                        "res": "OK",
                        "msg": "",
                        "installation": {
                            "numinst": "9999999",
                            "capabilities": "",
                            "services": [],
                            "configRepoUser": {
                                "alarmPartitions": [
                                    {
                                        "id": "01",
                                        "enterStates": ["01", "02"],
                                        "leaveStates": ["01", "02"],
                                    },
                                    {"id": "02", "enterStates": ["01"], "leaveStates": ["01"]},
                                    {"id": "03", "enterStates": [], "leaveStates": []},
                                ],
                            },
                        },
                    }
                }
            }
        )

        partitions = envelope.data.xSSrv.installation.config_repo_user.alarm_partitions
        assert len(partitions) == 3
        assert partitions[1].id == "02"
        assert partitions[1].enter_states == ("01",)
        assert partitions[2].enter_states == ()

    def test_parses_with_null_config_repo_user(self) -> None:
        """Issue #7 — some installs return configRepoUser=null. Must parse."""
        from verisure_italy.responses import ServicesEnvelope

        envelope = ServicesEnvelope.model_validate(
            {
                "data": {
                    "xSSrv": {
                        "res": "OK",
                        "msg": "",
                        "installation": {
                            "numinst": "9999999",
                            "capabilities": "",
                            "services": [],
                            "configRepoUser": None,
                        },
                    }
                }
            }
        )
        assert envelope.data.xSSrv.installation.config_repo_user is None

    def test_parses_with_missing_config_repo_user(self) -> None:
        """configRepoUser absent from payload — same as null."""
        from verisure_italy.responses import ServicesEnvelope

        envelope = ServicesEnvelope.model_validate(
            {
                "data": {
                    "xSSrv": {
                        "res": "OK",
                        "msg": "",
                        "installation": {
                            "numinst": "9999999",
                            "capabilities": "",
                            "services": [],
                        },
                    }
                }
            }
        )
        assert envelope.data.xSSrv.installation.config_repo_user is None
