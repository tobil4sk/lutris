import copy
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from lutris.database import games
from lutris.database.categories import (
    get_game_ids_for_categories,
    get_uncategorized_game_ids,
    normalized_category_names,
)
from lutris.runners.runner import Runner
from lutris.util.strings import strip_accents


def tokenize_search(text: str) -> Iterable[str]:
    def _tokenize():
        try:
            buffer = ""
            it = iter(text)
            while True:
                ch = next(it)
                if ch.isspace() != buffer.isspace():
                    yield buffer
                    buffer = ""
                elif ch == "-":
                    yield buffer
                    yield ch
                    buffer = ""
                    continue
                elif ch == ":":
                    buffer += ch
                    yield buffer
                    buffer = ""
                    continue
                elif ch == '"':
                    yield buffer

                    buffer = ch
                    while True:
                        ch = next(it)
                        buffer += ch

                        if ch == '"':
                            break

                    yield buffer
                    buffer = ""
                    continue

                buffer += ch
        except StopIteration:
            pass
        finally:
            yield buffer

    return filter(lambda t: len(t) > 0, _tokenize())


class BaseSearch:
    flag_texts = {"true": True, "yes": True, "false": False, "no": False, "maybe": None}
    tags = []

    def __init__(self, text: str) -> None:
        self.text = text
        self.predicates: Optional[List[Callable]] = None

    def __str__(self) -> str:
        return self.text

    @property
    def is_empty(self) -> bool:
        return not self.text

    def get_candidate_text(self, candidate: Any) -> str:
        return str(candidate)

    def get_components(self) -> List[Tuple[str, str, str, bool]]:
        def token_pairs():
            buffer = [""]
            for t in tokenize_search(self.text):
                if not t.isspace():
                    if len(buffer) == 3:
                        yield tuple(buffer)
                        del buffer[0]
                        buffer.append(t)
                    else:
                        buffer.append(t)

            while buffer:
                yield tuple(buffer + [""] * (3 - len(buffer)))
                del buffer[0]

        def clean_token(to_clean: str) -> str:
            if to_clean.startswith('"'):
                if to_clean.endswith('"'):
                    return to_clean[1:-1]
                else:
                    return to_clean[1:]
            return to_clean.strip()

        components = []
        skip_token = False
        for prev_token, token, next_token in token_pairs():
            if skip_token:
                skip_token = False
                continue

            negated = prev_token == "-"
            if token == "-":
                continue
            elif token.startswith('"'):
                unquoted = clean_token(token)
                components.append(("", unquoted, unquoted, negated))
                continue
            elif token.endswith(":"):
                name = token[:-1].strip().casefold()
                if name in self.tags:
                    value = clean_token(next_token)
                    components.append((name, value, token, negated))
                    skip_token = True
                    continue
            components.append(("", token, token, negated))

        return components

    def has_component(self, component_name: str) -> bool:
        for name, _value, _raw, _negated in self.get_components():
            if name == component_name:
                return True
        return False

    def get_predicates(self) -> List[Callable]:
        if self.predicates is None:
            predicates = []
            if self.text:
                for name, value, raw, negated in self.get_components():
                    if name:
                        predicate = self.get_part_predicate(name, value) or self.get_text_predicate(raw)
                    else:
                        predicate = self.get_text_predicate(raw)

                    if negated:
                        predicates.append(lambda *args, pred=predicate: not pred(*args))
                    else:
                        predicates.append(predicate)
            self.predicates = predicates
        return self.predicates

    def with_predicate(self, predicate: Callable):
        predicates = list(self.get_predicates())  # force generation of predicates & copy
        predicates.append(predicate)
        new_search = copy.copy(self)
        new_search.predicates = predicates
        return new_search

    def get_part_predicate(self, name: str, value: str) -> Optional[Callable]:
        return None

    def matches(self, candidate: Any) -> bool:
        for predicate in self.get_predicates():
            if not predicate(candidate):
                return False

        return True

    def get_text_predicate(self, text: str) -> Callable:
        stripped = strip_accents(text).casefold()

        def match_text(candidate):
            name = strip_accents(self.get_candidate_text(candidate)).casefold()
            return stripped in name

        return match_text


class GameSearch(BaseSearch):
    tags = ["installed", "hidden", "favorite", "categorized", "category", "runner", "platform"]

    def __init__(self, text: str, service) -> None:
        self.service = service
        super().__init__(text)

    def get_candidate_text(self, candidate: Any) -> str:
        return candidate["name"]

    def get_part_predicate(self, name: str, value: str) -> Optional[Callable]:
        folded_value = value.casefold()
        if folded_value in self.flag_texts:
            flag = self.flag_texts[folded_value]

            if flag is None:
                # None represents 'maybe' which performs no test, but overrides
                # the tests performed outside the search. Useful for 'hidden' and
                # 'installed' components
                return lambda *args: True

            if name == "installed":
                return self.get_installed_predicate(flag)

            if name == "hidden":
                return self.get_category_predicate(".hidden", in_category=flag)

            if name == "favorite":
                return self.get_category_predicate("favorite", in_category=flag)

            if name == "categorized":
                return self.get_categorized_predicate(flag)

        if name == "category":
            category = value.strip()
            return self.get_category_predicate(category)

        if name == "runner":
            runner_name = value.strip()
            return self.get_runner_predicate(runner_name)

        if name == "platform":
            platform = value.strip()
            return self.get_platform_predicate(platform)

        return super().get_part_predicate(name, value)

    def get_installed_predicate(self, installed: bool) -> Callable:
        def match_installed(db_game):
            is_installed = self._is_installed(db_game)
            return installed == is_installed

        return match_installed

    def _is_installed(self, db_game: Dict[str, Any]) -> bool:
        if self.service:
            appid = db_game.get("appid")
            return bool(appid and appid in games.get_service_games(self.service.id))

        return bool(db_game["installed"])

    def get_categorized_predicate(self, categorized: bool) -> Callable:
        uncategorized_ids = set(get_uncategorized_game_ids())

        def match_categorized(db_game):
            game_id = db_game["id"]
            is_categorized = game_id not in uncategorized_ids
            return is_categorized == categorized

        return match_categorized

    def get_category_predicate(self, category: str, in_category: bool = True) -> Callable:
        names = normalized_category_names(category)
        category_game_ids = set(get_game_ids_for_categories(names))

        def match_categorized(db_game):
            game_id = db_game["id"]
            game_in_category = game_id in category_game_ids
            return game_in_category == in_category

        return match_categorized

    def get_runner_predicate(self, runner_name: str) -> Callable:
        runner_name = runner_name.casefold()

        def match_runner(db_game):
            game_runner = db_game.get("runner")
            return game_runner and game_runner.casefold() == runner_name

        return match_runner

    def get_platform_predicate(self, platform: str) -> Callable:
        platform = platform.casefold()

        def match_platform(db_game):
            game_platform = db_game.get("platform")
            if game_platform:
                return game_platform.casefold() == platform
            if self.service:
                platforms = [p.casefold() for p in self.service.get_game_platforms(db_game)]
                return platform in platforms
            return False

        return match_platform


class RunnerSearch(BaseSearch):
    tags = ["installed"]

    def get_candidate_text(self, candidate: Any) -> str:
        return f"{candidate.name}\n{candidate.description}"

    def get_part_predicate(self, name: str, value: str) -> Optional[Callable]:
        if value in self.flag_texts:
            flag = self.flag_texts[value]
            if name == "installed":
                if flag is None:
                    return lambda *args: True

                return self.get_installed_predicate(flag)

        return super().get_part_predicate(name, value)

    def get_installed_predicate(self, installed: bool) -> Callable:
        def match_installed(runner: Runner):
            is_installed = runner.is_installed()
            return installed == is_installed

        return match_installed