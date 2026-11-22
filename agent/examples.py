"""One small bug per language.

Used three ways: as "Load example" buttons in the web page, as the
offline demo answer when no API key is set, and in the test suite, which
checks that every test fails on the buggy code and passes on the fix.
"""

EXAMPLES = {
    "python": {
        "source": '''def average_order_value(orders):
    total = sum(o["amount"] for o in orders)
    return round(total / len(orders), 2)


def apply_discount(price, code):
    discounts = {"SAVE10": 0.10, "SAVE20": 0.20}
    return round(price * (1 - discounts[code]), 2)
''',
        "error": '''Traceback (most recent call last):
  File "shop.py", line 3, in average_order_value
    return round(total / len(orders), 2)
ZeroDivisionError: division by zero''',
        "answer": {
            "summary": "The average order value crashes when there are no orders yet, for example on a new store or a quiet day.",
            "root_cause": "average_order_value() divides the total by len(orders). With an empty list, len(orders) is 0, so Python raises ZeroDivisionError.",
            "next_steps": ["Return 0 when there are no orders instead of dividing.", "Keep the new test so an empty list is always checked.", "apply_discount() raises KeyError for an unknown code. Worth fixing next."],
            "confidence": 94,
            "test_setup": "",
            "test_code": '''def test_average_with_no_orders():
    assert average_order_value([]) == 0


def test_average_still_works():
    assert average_order_value([{"amount": 10}, {"amount": 20}]) == 15
''',
            "fixed_source": '''def average_order_value(orders):
    if not orders:
        return 0
    total = sum(o["amount"] for o in orders)
    return round(total / len(orders), 2)


def apply_discount(price, code):
    discounts = {"SAVE10": 0.10, "SAVE20": 0.20}
    return round(price * (1 - discounts[code]), 2)
''',
            "pr_title": "Handle empty order list in average_order_value()",
        },
    },
    "javascript": {
        "source": '''function getShippingCity(user) {
  return user.address.city.toUpperCase();
}

function formatLabel(user) {
  return user.name + " - " + getShippingCity(user);
}
''',
        "error": '''TypeError: Cannot read properties of undefined (reading 'city')
    at getShippingCity (shipping.js:2:23)
    at formatLabel (shipping.js:6:30)''',
        "answer": {
            "summary": "Printing a shipping label crashes for customers who haven't saved an address yet.",
            "root_cause": "getShippingCity() reads user.address.city without checking that address exists. For a user with no address, user.address is undefined, so reading .city throws a TypeError.",
            "next_steps": ["Return a clear placeholder like UNKNOWN when there's no address.", "Keep the new test for a user without an address.", "Block checkout earlier if a shipping address is required."],
            "confidence": 93,
            "test_setup": "",
            "test_code": '''function test_user_without_address() {
  assert(formatLabel({ name: "Ana" }) === "Ana - UNKNOWN", "label should say UNKNOWN");
}

function test_user_with_address() {
  assert(formatLabel({ name: "Ben", address: { city: "Chicago" } }) === "Ben - CHICAGO", "label should show the city");
}
''',
            "fixed_source": '''function getShippingCity(user) {
  if (!user || !user.address || !user.address.city) return "UNKNOWN";
  return user.address.city.toUpperCase();
}

function formatLabel(user) {
  return user.name + " - " + getShippingCity(user);
}
''',
            "pr_title": "Handle users with no saved address in getShippingCity()",
        },
    },
    "sql": {
        "source": '''CREATE TABLE orders (id INTEGER, customer TEXT, amount REAL);
INSERT INTO orders VALUES (1, 'Ana', 40), (2, 'Ben', 60), (3, 'Ana', 25);

CREATE TABLE refunds (order_id INTEGER, amount REAL);
INSERT INTO refunds VALUES (3, 25);

-- Net revenue per customer after refunds
SELECT o.customer, SUM(o.amount) - SUM(r.amount) AS net
FROM orders o
LEFT JOIN refunds r ON r.order_id = o.id
GROUP BY o.customer;
''',
        "error": "Ben's net revenue comes back empty (NULL) instead of 60. Ana's is right.",
        "answer": {
            "summary": "Customers with no refunds show blank revenue instead of their real total.",
            "root_cause": "The query subtracts SUM(r.amount). For a customer with no refunds, the LEFT JOIN gives NULL refund amounts, so SUM(r.amount) is NULL, and anything minus NULL is NULL.",
            "next_steps": ["Treat missing refunds as 0 with COALESCE(SUM(r.amount), 0).", "Keep the new check for a customer with no refunds.", "Look for other reports that subtract a SUM from a LEFT JOIN."],
            "confidence": 95,
            "test_setup": "",
            "test_code": "SELECT (SELECT net FROM result WHERE customer = 'Ben') = 60\n   AND (SELECT net FROM result WHERE customer = 'Ana') = 40 AS ok;\n",
            "fixed_source": '''CREATE TABLE orders (id INTEGER, customer TEXT, amount REAL);
INSERT INTO orders VALUES (1, 'Ana', 40), (2, 'Ben', 60), (3, 'Ana', 25);

CREATE TABLE refunds (order_id INTEGER, amount REAL);
INSERT INTO refunds VALUES (3, 25);

-- Net revenue per customer after refunds
SELECT o.customer, SUM(o.amount) - COALESCE(SUM(r.amount), 0) AS net
FROM orders o
LEFT JOIN refunds r ON r.order_id = o.id
GROUP BY o.customer;
''',
            "pr_title": "Treat missing refunds as 0 in net revenue query",
        },
    },
    "c": {
        "source": '''double average(const int *values, int count) {
    int total = 0;
    for (int i = 0; i < count; i++) {
        total += values[i];
    }
    return total / count;
}
''',
        "error": "Floating point exception (core dumped) when count is 0. Also average of {1, 2, 2} returns 1.000000 instead of 1.666667",
        "answer": {
            "summary": "The average function crashes on an empty list and gives wrong answers for normal lists.",
            "root_cause": "average() divides two ints, so C does whole-number division and drops the fraction (5 / 3 = 1). When count is 0, integer division by zero crashes the program with a floating point exception.",
            "next_steps": ["Return 0.0 when count is 0 or less.", "Convert total to double before dividing.", "Keep both new checks in the test."],
            "confidence": 95,
            "test_setup": "",
            "test_code": '''#include <stdio.h>
#include <math.h>

int main(void) {
    int none[1] = {0};
    if (average(none, 0) != 0.0) { printf("empty list should give 0\\n"); return 1; }
    int v[3] = {1, 2, 2};
    if (fabs(average(v, 3) - 5.0 / 3.0) > 1e-9) { printf("average of 1,2,2 is wrong\\n"); return 1; }
    return 0;
}
''',
            "fixed_source": '''double average(const int *values, int count) {
    if (count <= 0) {
        return 0.0;
    }
    int total = 0;
    for (int i = 0; i < count; i++) {
        total += values[i];
    }
    return (double)total / count;
}
''',
            "pr_title": "Fix integer division and empty input in average()",
        },
    },
    "cpp": {
        "source": '''#include <vector>

int lastReading(const std::vector<int>& readings) {
    return readings.at(readings.size() - 1);
}
''',
        "error": "terminate called after throwing an instance of 'std::out_of_range' when the sensor has no readings yet",
        "answer": {
            "summary": "Asking for the latest sensor reading crashes the program when the sensor hasn't sent anything yet.",
            "root_cause": "lastReading() asks for the item at size() - 1. For an empty vector, size() is 0, so size() - 1 wraps around to a huge number and .at() throws std::out_of_range, which nothing catches.",
            "next_steps": ["Return 0 (or a 'no reading' value) when the vector is empty.", "Keep the new test for an empty sensor.", "Decide with the team whether callers should get an optional value instead of 0."],
            "confidence": 92,
            "test_setup": "",
            "test_code": '''#include <cstdio>

int main() {
    if (lastReading({}) != 0) { std::printf("empty sensor should give 0\\n"); return 1; }
    if (lastReading({3, 5, 8}) != 8) { std::printf("latest reading should be 8\\n"); return 1; }
    return 0;
}
''',
            "fixed_source": '''#include <vector>

int lastReading(const std::vector<int>& readings) {
    if (readings.empty()) {
        return 0;
    }
    return readings.back();
}
''',
            "pr_title": "Handle sensors with no readings in lastReading()",
        },
    },
    "java": {
        "source": '''public class Inventory {
    public static int averageStock(int[] counts) {
        int total = 0;
        for (int c : counts) total += c;
        return total / counts.length;
    }
}
''',
        "error": '''Exception in thread "main" java.lang.ArithmeticException: / by zero
    at Inventory.averageStock(Inventory.java:5)''',
        "answer": {
            "summary": "The stock report crashes for a warehouse with no items yet.",
            "root_cause": "averageStock() divides by counts.length. For an empty array that's 0, and integer division by zero throws ArithmeticException.",
            "next_steps": ["Return 0 when the array is empty.", "Keep the new test for an empty warehouse.", "Check other reports that divide by an array length."],
            "confidence": 94,
            "test_setup": "",
            "test_code": '''public class TestMain {
    public static void main(String[] args) {
        if (Inventory.averageStock(new int[]{}) != 0) {
            System.out.println("empty warehouse should average 0");
            System.exit(1);
        }
        if (Inventory.averageStock(new int[]{4, 6}) != 5) {
            System.out.println("average of 4 and 6 should be 5");
            System.exit(1);
        }
    }
}
''',
            "fixed_source": '''public class Inventory {
    public static int averageStock(int[] counts) {
        if (counts.length == 0) return 0;
        int total = 0;
        for (int c : counts) total += c;
        return total / counts.length;
    }
}
''',
            "pr_title": "Handle empty inventory in averageStock()",
        },
    },
    "go": {
        "source": '''package words

func firstWord(words []string) string {
	return words[0]
}
''',
        "error": "panic: runtime error: index out of range [0] with length 0",
        "answer": {
            "summary": "Getting the first word crashes the program when the list of words is empty.",
            "root_cause": "firstWord() reads words[0] without checking the length. For an empty slice there is no index 0, so Go panics with index out of range.",
            "next_steps": ["Return an empty string when the slice is empty.", "Keep the new test for an empty slice.", "Look for other places that read [0] without a length check."],
            "confidence": 95,
            "test_setup": "",
            "test_code": '''package words

import "testing"

func TestFirstWordEmpty(t *testing.T) {
	if got := firstWord([]string{}); got != "" {
		t.Fatalf("want empty string, got %q", got)
	}
}

func TestFirstWord(t *testing.T) {
	if got := firstWord([]string{"hello", "there"}); got != "hello" {
		t.Fatalf("want hello, got %q", got)
	}
}
''',
            "fixed_source": '''package words

func firstWord(words []string) string {
	if len(words) == 0 {
		return ""
	}
	return words[0]
}
''',
            "pr_title": "Handle empty slice in firstWord()",
        },
    },
    "r": {
        "source": '''average_score <- function(scores) {
  sum(scores) / length(scores)
}

pass_rate <- function(scores, cutoff = 70) {
  mean(scores >= cutoff)
}
''',
        "error": "average_score(c(80, NA, 90)) returns NA instead of 85",
        "answer": {
            "summary": "One missing score makes the whole class average come back as NA.",
            "root_cause": "average_score() uses sum(scores), and in R any sum that includes NA is NA. It also divides by length(scores), which counts the missing score.",
            "next_steps": ["Ignore missing values: sum(scores, na.rm = TRUE) divided by the number of real scores.", "Keep the new test with a missing score.", "pass_rate() has the same NA problem. Fix it next."],
            "confidence": 93,
            "test_setup": "",
            "test_code": '''test_ignores_missing <- function() {
  stopifnot(isTRUE(average_score(c(80, NA, 90)) == 85))
}

test_normal_scores <- function() {
  stopifnot(isTRUE(average_score(c(70, 90)) == 80))
}
''',
            "fixed_source": '''average_score <- function(scores) {
  present <- scores[!is.na(scores)]
  if (length(present) == 0) return(NA_real_)
  sum(present) / length(present)
}

pass_rate <- function(scores, cutoff = 70) {
  mean(scores >= cutoff)
}
''',
            "pr_title": "Ignore missing scores in average_score()",
        },
    },
}
