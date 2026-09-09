// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include "Base/Exception.h"
#include "Base/Persistence.h"
#include "Base/Writer.h"

#include <thread>
#ifdef _MSC_VER
#include <zipios++/zipios-config.h>
#endif
#include <zipios++/zipinputstream.h>

// Writer is designed to be a base class, so for testing we actually instantiate a StringWriter,
// which is derived from it

class WriterTest: public ::testing::Test
{
protected:
    // void SetUp() override {}

    // void TearDown() override {}
protected:
    Base::StringWriter _writer;
};

TEST_F(WriterTest, insertTextSimple)
{
    // Arrange
    std::string testTextData {"Simple ASCII data"};
    std::string expectedResult {"<![CDATA[" + testTextData + "]]>"};

    // Act
    _writer.insertText(testTextData);

    // Assert
    EXPECT_EQ(expectedResult, _writer.getString());
}

/// If the data happens to actually include an XML CDATA close marker, that needs to be "escaped" --
/// this is done by breaking it up into two separate CDATA sections, splitting apart the marker.
TEST_F(WriterTest, insertTextNeedsEscape)
{
    // Arrange
    std::string testDataA {"ASCII data with a close marker in it, like so: ]]"};
    std::string testDataB {"> "};
    std::string expectedResult {"<![CDATA[" + testDataA + "]]><![CDATA[" + testDataB + "]]>"};

    // Act
    _writer.insertText(testDataA + testDataB);

    // Assert
    EXPECT_EQ(expectedResult, _writer.getString());
}

TEST_F(WriterTest, insertNonAsciiData)
{
    // Arrange
    std::string testData {"\x01\x02\x03\x04\u0001F450😀"};
    std::string expectedResult {"<![CDATA[" + testData + "]]>"};

    // Act
    _writer.insertText(testData);

    // Assert
    EXPECT_EQ(expectedResult, _writer.getString());
}

TEST_F(WriterTest, beginCharStream)
{
    // Arrange & Act
    auto& checkStream {_writer.beginCharStream()};

    // Assert
    EXPECT_TRUE(checkStream.good());
}

TEST_F(WriterTest, beginCharStreamTwice)
{
    // Arrange
    _writer.beginCharStream();

    // Act & Assert
    EXPECT_THROW(_writer.beginCharStream(), Base::RuntimeError);
}

TEST_F(WriterTest, endCharStream)
{
    // Arrange
    _writer.beginCharStream();

    // Act
    _writer.endCharStream();

    // Assert
    EXPECT_EQ("<![CDATA[]]>", _writer.getString());
}

TEST_F(WriterTest, endCharStreamTwice)
{
    // Arrange
    _writer.beginCharStream();
    _writer.endCharStream();

    // Act
    _writer.endCharStream();  // Doesn't throw, or do anything at all

    // Assert
    EXPECT_EQ("<![CDATA[]]>", _writer.getString());
}

TEST_F(WriterTest, charStream)
{
    // Arrange
    auto& streamA {_writer.beginCharStream()};

    // Act
    auto& streamB {_writer.charStream()};

    // Assert
    EXPECT_EQ(&streamA, &streamB);
}

TEST_F(WriterTest, charStreamBase64Encoded)
{
    // Arrange
    _writer.beginCharStream(Base::CharStreamFormat::Base64Encoded);
    std::string data {"FreeCAD rocks! 🪨🪨🪨"};

    // Act
    _writer.charStream() << data;
    _writer.endCharStream();

    // Assert
    // Conversion done using https://www.base64encode.org for testing purposes
    EXPECT_EQ(std::string("RnJlZUNBRCByb2NrcyEg8J+qqPCfqqjwn6qo\n"), _writer.getString());
}

TEST_F(WriterTest, ownerCaptureDoesNotWriteDestinationOnOwner)
{
    const auto caller = std::this_thread::get_id();
    int dispatched = 0;
    auto dispatch = [&](std::function<void()> capture) {
        std::exception_ptr failure;
        std::thread owner([&] {
            ++dispatched;
            EXPECT_NE(std::this_thread::get_id(), caller);
            try {
                capture();
            }
            catch (...) {
                failure = std::current_exception();
            }
        });
        owner.join();
        if (failure) {
            std::rethrow_exception(failure);
        }
    };
    _writer.Stream() << "before:";
    _writer.captureOnOwner(dispatch, [&] {
        _writer.beginCharStream() << "captured";
        _writer.endCharStream();
        // No owner write may have reached the destination stream buffer.
        EXPECT_EQ(_writer.getString(), "before:");
    });
    EXPECT_EQ(_writer.getString(), "before:<![CDATA[captured]]>");
    EXPECT_EQ(dispatched, 1);

    EXPECT_THROW(
        _writer.captureOnOwner(
            dispatch,
            [&] {
                _writer.Stream() << "discarded";
                throw std::runtime_error("capture failed");
            }
        ),
        std::runtime_error
    );
    _writer.Stream() << ":after";
    EXPECT_EQ(_writer.getString(), "before:<![CDATA[captured]]>:after");
}

TEST_F(WriterTest, nestedBinaryFilesRetainCaptureOwner)
{
    struct Payload final: Base::Persistence
    {
        std::thread::id caller;
        const Payload* child {nullptr};
        mutable int writes {0};
        unsigned int getMemSize() const override
        {
            return 0;
        }
        void Restore(Base::XMLReader&) override
        {}
        void Save(Base::Writer& writer) const override
        {
            writer.addFile("payload.bin", this);
        }
        void SaveDocFile(Base::Writer& writer) const override
        {
            EXPECT_NE(std::this_thread::get_id(), caller);
            ++writes;
            writer.Stream().write("a\0b", 3);
            if (child) {
                child->Save(writer);
            }
        }
    };
    Payload parent, child;
    parent.caller = child.caller = std::this_thread::get_id();
    parent.child = &child;
    std::ostringstream archive;
    {
        Base::ZipWriter writer(archive);
        writer.putNextEntry("root.xml");
        writer.captureOnOwner(
            [](std::function<void()> capture) {
                std::exception_ptr failure;
                std::thread owner([&] {
                    try {
                        capture();
                    }
                    catch (...) {
                        failure = std::current_exception();
                    }
                });
                owner.join();
                if (failure) {
                    std::rethrow_exception(failure);
                }
            },
            [&] {
                writer.Stream() << "root";
                parent.Save(writer);
            }
        );
        writer.writeFiles();
    }
    EXPECT_EQ(parent.writes, 1);
    EXPECT_EQ(child.writes, 1);
    std::istringstream input(archive.str());
    zipios::ZipInputStream zip(input);
    std::ostringstream root;
    root << zip.rdbuf();
    EXPECT_EQ(root.str(), "root");
    std::vector<std::string> names;
    // ZipInputStream reports the central directory with an exception rather
    // than an EOF entry. Read the two registered payloads explicitly.
    for (int index = 0; index < 2; ++index) {
        auto entry = zip.getNextEntry();
        ASSERT_TRUE(entry->isValid());
        names.push_back(entry->getName());
        std::ostringstream bytes;
        bytes << zip.rdbuf();
        EXPECT_EQ(bytes.str(), std::string("a\0b", 3));
    }
    ASSERT_EQ(names.size(), 2);
    EXPECT_NE(names[0], names[1]);
}
