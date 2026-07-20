from app.core.models import Task, TaskConfirm, TaskContract, TaskContractItem, User, new_id, utc_now


class TaskContractService:
    def confirm_contract(
        self,
        task: Task,
        payload: TaskConfirm,
        confirmed_by: User,
    ) -> TaskContract:
        if payload.contract is None:
            return self._legacy_contract(payload, confirmed_by)

        contract = payload.contract
        return TaskContract(
            goal=contract.goal,
            deliverable_goal=contract.deliverable_goal,
            deliverable_requirements=self._items_with_ids(contract.deliverable_requirements, "requirement"),
            success_criteria=self._items_with_ids(contract.success_criteria, "criterion"),
            requires_human_acceptance=contract.requires_human_acceptance,
            confirmed_by_user_id=confirmed_by.id,
            confirmed_by_user_name=confirmed_by.name,
            confirmed_at=utc_now(),
        )

    def _legacy_contract(
        self,
        payload: TaskConfirm,
        confirmed_by: User,
    ) -> TaskContract:
        return TaskContract(
            goal=payload.description,
            deliverable_goal=payload.title,
            deliverable_requirements=[],
            success_criteria=self._items_from_text(
                [f"已产生与确认目标“{payload.description}”一致的可审核结果"],
                "criterion",
            ),
            requires_human_acceptance=False,
            confirmed_by_user_id=confirmed_by.id,
            confirmed_by_user_name=confirmed_by.name,
            confirmed_at=utc_now(),
            legacy_inferred=True,
        )

    @staticmethod
    def _items_with_ids(items: list[TaskContractItem], prefix: str) -> list[TaskContractItem]:
        return [
            TaskContractItem(id=item.id or new_id(prefix), description=item.description)
            for item in items
        ]

    @classmethod
    def _items_from_text(cls, descriptions: list[str], prefix: str) -> list[TaskContractItem]:
        return cls._items_with_ids(
            [TaskContractItem(description=description) for description in descriptions],
            prefix,
        )
