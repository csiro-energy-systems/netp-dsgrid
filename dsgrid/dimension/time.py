"""Dimensions related to time"""
import datetime
import pytz

from dsgrid.data_models import DSGEnum, EnumValue


class LeapDayAdjustmentType(DSGEnum):
    """Timezone enum types"""

    DROP_DEC31 = EnumValue(
        value="drop_dec31",
        description="To adjust for leap years, December 31st gets dropped",
    )
    DROP_FEB29 = EnumValue(
        value="drop_feb29",
        description="Feburary 29th is dropped. Currently not yet supported by dsgrid.",
    )
    DROP_JAN1 = EnumValue(
        value="drop_jan1",
        description="To adjust for leap years, January 1st gets dropped",
    )


class Period(DSGEnum):
    """Time period enum types"""

    # TODO: R2PD uses a different set; do we want to align?
    # https://github.com/Smart-DS/R2PD/blob/master/R2PD/tshelpers.py#L15

    PERIOD_ENDING = EnumValue(
        value="period_ending",
        description="A time period that is period ending is coded by the end time. E.g., 2pm (with"
        " freq=1h) represents a period of time between 1-2pm.",
    )
    PERIOD_BEGINNING = EnumValue(
        value="period_beginning",
        description="A time period that is period beginning is coded by the beginning time. E.g., "
        "2pm (with freq=1h) represents a period of time between 2-3pm. This is the dsgrid default.",
    )
    INSTANTANEOUS = EnumValue(
        value="instantaneous",
        description="The time record value represents measured, instantaneous time",
    )


class TimeValueMeasurement(DSGEnum):
    """Time value measurement enum types"""

    MEAN = EnumValue(
        value="mean",
        description="Data values represent the average value in a time range",
    )
    MIN = EnumValue(
        value="min",
        description="Data values represent the minimum value in a time range",
    )
    MAX = EnumValue(
        value="max",
        description="Data values represent the maximum value in a time range",
    )
    MEASURED = EnumValue(
        value="measured",
        description="Data values represent the measured value at that reported time",
    )
    TOTAL = EnumValue(
        value="total",
        description="Data values represent the sum of values in a time range",
    )


class TimezoneType(DSGEnum):
    """Timezone enum types"""

    UTC = EnumValue(
        value="UTC",
        description="Coordinated Universal Time",
        tz=datetime.timezone(datetime.timedelta()),
    )
    HST = EnumValue(
        value="HawaiiAleutianStandard",
        description="Hawaii Standard Time (UTC=-10). Does not include DST shifts.",
        tz=datetime.timedelta(hours=-10),
    )
    AST = EnumValue(
        value="AlaskaStandard",
        description="Alaskan Standard Time (UTC=-9). Does not include DST shifts.",
        tz=datetime.timezone(datetime.timedelta(hours=-9)),
    )
    APT = EnumValue(
        value="AlaskaPrevailingStandard",
        description="Alaska Prevailing Time. Commonly called Alaska Local Time. Includes DST"
        " shifts during DST times.",
        tz=pytz.timezone("US/Alaska"),
    )
    PST = EnumValue(
        value="PacificStandard",
        description="Pacific Standard Time (UTC=-8). Does not include DST shifts.",
        tz=datetime.timezone(datetime.timedelta(hours=-8)),
    )
    PPT = EnumValue(
        value="PacificPrevailing",
        description="Pacific Prevailing Time. Commonly called Pacific Local Time. Includes DST"
        " shifts ,during DST times.",
        tz=pytz.timezone("US/Pacific"),
    )
    MST = EnumValue(
        value="MountainStandard",
        description="Mountain Standard Time (UTC=-7). Does not include DST shifts.",
        tz=datetime.timezone(datetime.timedelta(hours=-7)),
    )
    MPT = EnumValue(
        value="MountainPrevailing",
        description="Mountain Prevailing Time. Commonly called Mountain Local Time. Includes DST"
        " shifts during DST times.",
        tz=pytz.timezone("US/Mountain"),
    )
    CST = EnumValue(
        value="CentralStandard",
        description="Central Standard Time (UTC=-6). Does not include DST shifts.",
        tz=datetime.timezone(datetime.timedelta(hours=-6)),
    )
    CPT = EnumValue(
        value="CentralPrevailing",
        description="Central Prevailing Time. Commonly called Central Local Time. Includes DST"
        " shifts during DST times.",
        tz=pytz.timezone("US/Central"),
    )
    EST = EnumValue(
        value="EasternStandard",
        description="Eastern Standard Time (UTC=-5). Does not include DST shifts.",
        tz=datetime.timezone(datetime.timedelta(hours=-5)),
    )
    EPT = EnumValue(
        value="EasternPrevailing",
        description="Eastern Prevailing Time. Commonly called Eastern Local Time. Includes DST"
        " shifts during DST times.",
        tz=pytz.timezone("US/Eastern"),
    )
    LOCAL = EnumValue(
        value="LOCAL",
        description="Local time. Implies that the geography's timezone will be dynamically applied"
        " when converting loca time to other time zones.",
        tz=None,  # TODO: needs handling: DSGRID-171
    )


class DatetimeRange:
    def __init__(self, start, end, frequency):
        self.start = start
        self.end = end
        self.frequency = frequency

    def iter_time_range(self, period: Period, leap_day_adjustment: LeapDayAdjustmentType):
        """Return a generator of datetimes for a time range.

        Parameters
        ----------
        period : Period
        leap_day_adjustment : LeapDayAdjustmentType

        Yields
        ------
        datetime

        """
        cur = self.start
        end = self.end + self.frequency if period == period.PERIOD_ENDING else self.end
        while cur < end:
            if not (
                leap_day_adjustment == LeapDayAdjustmentType.DROP_FEB29
                and cur.month == 2
                and cur.day == 29
            ):
                yield cur
            cur += self.frequency

    def list_time_range(self, period: Period, leap_day_adjustment: LeapDayAdjustmentType):
        """Return a list of datetimes for a time range.

        Parameters
        ----------
        period : Period
        leap_day_adjustment : LeapDayAdjustmentType

        Returns
        -------
        list
            list of datetime

        """
        return list(self.iter_time_range(period, leap_day_adjustment))
