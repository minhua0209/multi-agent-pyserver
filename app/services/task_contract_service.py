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
        acceptance_criteria = self._merge_acceptance_items(
            contract.deliverable_requirements,
            contract.success_criteria,
        )
        return TaskContract(
            version=2,
            goal=contract.goal,
            deliverable_goal=contract.deliverable_goal,
            deliverable_kind=contract.deliverable_kind,
            deliverable_format=contract.deliverable_format,
            deliverable_filename=contract.deliverable_filename,
            deliverable_requirements=[],
            success_criteria=acceptance_criteria,
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

    @staticmethod
    def _merge_acceptance_items(
        deliverable_requirements: list[TaskContractItem],
        success_criteria: list[TaskContractItem],
    ) -> list[TaskContractItem]:
        merged: list[TaskContractItem] = []
        seen_descriptions: set[str] = set()
        seen_ids: set[str] = set()
        for item in [*deliverable_requirements, *success_criteria]:
            if len(merged) >= 10:
                break
            description_key = item.description.strip().casefold()
            if description_key in seen_descriptions:
                continue
            item_id = item.id if item.id and item.id not in seen_ids else new_id("criterion")
            merged.append(TaskContractItem(id=item_id, description=item.description))
            seen_descriptions.add(description_key)
            seen_ids.add(item_id)
        return merged
