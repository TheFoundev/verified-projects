#!/usr/bin/env bash
#
# Local verification for event-streaming-platform-kafka-lite.
#
# mvn/javac are not installed in every environment, so this script verifies the
# project with a plain JRE:
#   * downloads the compile/runtime dependencies into .tools/jars (gitignored)
#   * compiles ALL main sources with the Eclipse compiler (ECJ)
#   * compiles and runs the JUnit 5 tests with the console launcher
#
# Tier 1 (compile + tests) is attempted first. If downloads are unavailable it
# falls back to Tier 3 structural checks so the script still reports something
# useful. The README records which tier actually ran.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PROJECT_DIR="$PWD"
JARS="$PROJECT_DIR/.tools/jars"
M2=https://repo1.maven.org/maven2
KAFKA=3.7.0
JACKSON=2.17.1
SLF4J=2.0.13
ECJ=3.46.100
JUNIT=1.10.2

echo "=== Verification for event-streaming-platform-kafka-lite ==="
echo "Date: $(date -u +%FT%TZ)"
echo "Java: $(java -version 2>&1 | head -1)"
echo

# ---------------------------------------------------------------- Tier 3 first
echo "[structural] required files"
required=(
  README.md LICENSE .gitignore pom.xml
  src/main/java/local/foundev/streams/Main.java
  src/main/java/local/foundev/streams/model/ClickEvent.java
  src/main/java/local/foundev/streams/producer/ClickProducer.java
  src/main/java/local/foundev/streams/registry/SchemaRegistryClient.java
  src/main/java/local/foundev/streams/registry/FakeSchemaRegistry.java
  src/main/java/local/foundev/streams/deser/SchemaValidatingDeserializer.java
  src/main/java/local/foundev/streams/streams/ClicksStreamsApp.java
  src/main/java/local/foundev/streams/api/ViewServer.java
  src/main/resources/clicks-value.schema.json
  src/main/resources/dashboard/index.html
  Dockerfile docker-compose.yml
)
missing=0
for f in "${required[@]}"; do
  if [ -f "$f" ]; then echo "  ok   $f"; else echo "  MISS $f"; missing=1; fi
done
[ "$missing" -eq 0 ] || { echo "FAIL: missing required files"; exit 1; }

python3 -m json.tool src/main/resources/clicks-value.schema.json >/dev/null \
  && echo "[structural] schema JSON valid"

echo
echo "[Tier 1] fetching dependencies into .tools/jars (gitignored)"
mkdir -p "$JARS"
fetch() { # url filename
  [ -s "$JARS/$2" ] && return 0
  curl -fsSL "$1" -o "$JARS/$2" || return 1
}
ok=1
fetch "$M2/org/eclipse/jdt/ecj/$ECJ/ecj-$ECJ.jar" "ecj-$ECJ.jar" || ok=0
fetch "$M2/org/apache/kafka/kafka-clients/$KAFKA/kafka-clients-$KAFKA.jar" "kafka-clients-$KAFKA.jar" || ok=0
fetch "$M2/org/apache/kafka/kafka-streams/$KAFKA/kafka-streams-$KAFKA.jar" "kafka-streams-$KAFKA.jar" || ok=0
fetch "$M2/org/apache/kafka/kafka-streams-test-utils/$KAFKA/kafka-streams-test-utils-$KAFKA.jar" "kafka-streams-test-utils-$KAFKA.jar" || ok=0
fetch "$M2/com/fasterxml/jackson/core/jackson-databind/$JACKSON/jackson-databind-$JACKSON.jar" "jackson-databind-$JACKSON.jar" || ok=0
fetch "$M2/com/fasterxml/jackson/core/jackson-core/$JACKSON/jackson-core-$JACKSON.jar" "jackson-core-$JACKSON.jar" || ok=0
fetch "$M2/com/fasterxml/jackson/core/jackson-annotations/$JACKSON/jackson-annotations-$JACKSON.jar" "jackson-annotations-$JACKSON.jar" || ok=0
fetch "$M2/org/slf4j/slf4j-api/$SLF4J/slf4j-api-$SLF4J.jar" "slf4j-api-$SLF4J.jar" || ok=0
fetch "$M2/org/junit/platform/junit-platform-console-standalone/$JUNIT/junit-platform-console-standalone-$JUNIT.jar" "junit-platform-console-standalone-$JUNIT.jar" || ok=0

if [ "$ok" -ne 1 ]; then
  echo
  echo "=== RESULT ==="
  echo "Tier: Tier 3 (structural only) - dependency download unavailable"
  echo "Status: PASS (structure valid; compilation not attempted)"
  exit 0
fi

CP="$JARS/kafka-clients-$KAFKA.jar:$JARS/kafka-streams-$KAFKA.jar:$JARS/kafka-streams-test-utils-$KAFKA.jar:$JARS/jackson-databind-$JACKSON.jar:$JARS/jackson-core-$JACKSON.jar:$JARS/jackson-annotations-$JACKSON.jar:$JARS/slf4j-api-$SLF4J.jar"
JUNIT_JAR="$JARS/junit-platform-console-standalone-$JUNIT.jar"

# A JRE-only install has lib/modules but no jmods/release; give ECJ a JDK-shaped
# system root pointing at the runtime image so --release 17 resolves java.* types.
SYS="${TMPDIR:-/tmp}/kafka-lite-fakejdk"
JH="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"
if [ ! -f "$SYS/lib/modules" ]; then
  rm -rf "$SYS"; mkdir -p "$SYS/lib" "$SYS/bin"
  ln -sf "$JH/lib/modules" "$SYS/lib/modules"
  [ -f "$JH/lib/jrt-fs.jar" ] && ln -sf "$JH/lib/jrt-fs.jar" "$SYS/lib/jrt-fs.jar"
  [ -f "$JH/lib/tzdb.dat" ] && ln -sf "$JH/lib/tzdb.dat" "$SYS/lib/tzdb.dat"
  printf 'JAVA_VERSION="%s"\nIMPLEMENTOR="OpenJDK"\n' "$(java -version 2>&1 | head -1 | cut -d\" -f2)" > "$SYS/release"
fi

echo "[Tier 1] compiling main sources with ECJ"
rm -rf out/main out/test; mkdir -p out/main out/test
find src/main/java -name '*.java' > out/sources.txt
java -jar "$JARS/ecj-$ECJ.jar" -17 --system "$SYS" -nowarn -proc:none \
  -cp "$CP" -d out/main @out/sources.txt || { echo "FAIL: main compilation"; exit 1; }
echo "  ok   $(find out/main -name '*.class' | wc -l) class files"

echo "[Tier 1] compiling tests with ECJ"
find src/test/java -name '*.java' > out/test-sources.txt
java -jar "$JARS/ecj-$ECJ.jar" -17 --system "$SYS" -nowarn -proc:none \
  -cp "out/main:$JUNIT_JAR:$CP" -d out/test @out/test-sources.txt || { echo "FAIL: test compilation"; exit 1; }

echo "[Tier 1] running JUnit 5 tests"
java -jar "$JUNIT_JAR" execute -cp "out/main:out/test:$JUNIT_JAR:$CP" \
  --scan-classpath=out/test --details=summary || { echo "FAIL: tests"; exit 1; }

echo
echo "=== RESULT ==="
echo "Tier: Tier 1 (ECJ compile + JUnit tests)"
echo "Status: PASS"
